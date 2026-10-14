"""Bounded background worker, durable checkpoints and repairable file exports."""
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

from .cache import atomic_write_json, atomic_write_text, atomic_copy
from .job_store import JobStore, JobStopped, ConflictError
from .safety import resolve_under_base
from .artifacts import _summarize_review, _summarize_plagiarism

log = logging.getLogger(__name__)


def discard_staging(root, staging_id):
    """Delete only an application-created UUID upload directory under its root."""
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", staging_id):
        raise ValueError("invalid staging id")
    base = (Path(root) / ".stylemuse" / "uploads").resolve()
    target = resolve_under_base(base, base / staging_id)
    if target == base:
        raise ValueError("cannot delete upload root")
    if target.exists():
        shutil.rmtree(target)


class JobService:
    def __init__(self, root, skill_factory, db_path=None, exporter=None):
        self.root = Path(root).resolve()
        self.store = JobStore(db_path or self.root / ".stylemuse" / "state.sqlite3")
        self.skill_factory = skill_factory
        self.exporter = exporter or self._export
        self.owner = uuid.uuid4().hex
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.heartbeat_thread = None
        self._start_lock = threading.Lock()
        self._execution_lock = threading.Lock()

    def start(self):
        with self._start_lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop.clear()
            self.store.heartbeat(self.owner)
            self.store.recover()
            self.heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True, name="stylemuse-heartbeat")
            self.thread = threading.Thread(target=self._run, daemon=True, name="stylemuse-jobs")
            self.heartbeat_thread.start()
            self.thread.start()

    def close(self, timeout=3):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(1)

    def _heartbeat(self):
        while not self.stop.wait(5):
            if self.thread and not self.thread.is_alive():
                return
            try:
                self.store.heartbeat(self.owner)
            except Exception:
                log.exception("Worker heartbeat failed")

    def _run(self):
        while not self.stop.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                log.exception("Job queue failed")
            self.wake.wait(0.5)
            self.wake.clear()

    def run_once(self):
        with self._execution_lock:
            return self._execute_next()

    def _execute_next(self):
        self.store.heartbeat(self.owner)
        self.store.recover()
        self.store.abandon_owner(self.owner)
        job = self.store.claim(self.owner)
        if job is None:
            return False
        try:
            if job["kind"] == "import":
                self._import(job)
            else:
                self._generate(job)
        except JobStopped:
            self.store.finish(job["id"], self.owner, "interrupted", "任务处理已中断，草稿已保留")
        except Exception as exc:
            log.exception("Job %s failed at %s", job["id"], self.store.get(job["id"])["stage"])
            self.store.finish(job["id"], self.owner, "failed", str(exc))
        return True

    def _check(self, job):
        if self.stop.is_set():
            raise JobStopped("worker stopping")
        self.store.ensure_running(job["id"], self.owner)

    def _stage(self, job, stage):
        self._check(job)
        self.store.checkpoint(job["id"], self.owner, stage=stage)

    def _generate(self, job):
        payload = job["payload"]
        skill = None
        if not job["generation_complete"] and job["kind"] == "restore":
            source = self.store.get_version(payload["restored_from"])
            self.store.checkpoint(job["id"], self.owner, article=source["article"], generation_complete=True,
                                  checks_complete=True, review=source["review"], plagiarism=source["plagiarism"],
                                  options=source["request"])
        else:
            if not job["generation_complete"] or not job["checks_complete"]:
                self._stage(job, "retrieving")
                kwargs = {key: payload[key] for key in ("model", "temperature", "max_tokens") if payload.get(key) is not None and payload[key] != ""}
                skill = self.skill_factory(job["author"], **kwargs)
                options = {"tone": payload.get("tone", "default"), "length": payload.get("length", "medium"),
                           "include_retrieval": payload.get("include_retrieval", True)}
                config = getattr(skill, "config", {}) or {}
                options.update({"model": config.get("llm_model", payload.get("model", "")),
                                "provider": config.get("llm_provider", ""),
                                "temperature": config.get("temperature", payload.get("temperature")),
                                "max_tokens": config.get("max_tokens", payload.get("max_tokens"))})
                self.store.checkpoint(job["id"], self.owner, options=options)
            if not job["generation_complete"]:
                self._stage(job, "generating")
                args = {"topic": job["topic"], "tone": payload.get("tone", "default"),
                        "length": payload.get("length", "medium"), "include_retrieval": payload.get("include_retrieval", True)}
                if job["kind"] == "rewrite":
                    parent = self.store.get_version(payload["parent_version"])
                    content = skill.rewrite(original_article=parent["article"], review=parent["review"] or {}, run_checks=False, **args)
                    self._check(job)
                    self.store.checkpoint(job["id"], self.owner, article=content, generation_complete=True)
                else:
                    pending = ""
                    last_flush = time.monotonic()
                    stream = skill.write_stream(run_checks=False, **args)
                    try:
                        for chunk in stream:
                            self._check(job)
                            pending += chunk
                            if len(pending) >= 80 or time.monotonic() - last_flush >= 0.12:
                                self.store.append(job["id"], self.owner, pending)
                                pending = ""
                                last_flush = time.monotonic()
                        if pending:
                            self.store.append(job["id"], self.owner, pending)
                            pending = ""
                    except Exception:
                        if pending:
                            self.store.append(job["id"], self.owner, pending)
                        raise
                    finally:
                        if hasattr(stream, "close"):
                            stream.close()
                    self.store.checkpoint(job["id"], self.owner, generation_complete=True)
            checkpoint = self.store.get(job["id"])
            if not checkpoint["checks_complete"]:
                self._stage(job, "checking")
                skill._run_post_generation_checks(checkpoint["article"], job["topic"], payload.get("tone", "default"), payload.get("length", "medium"))
                self.store.checkpoint(job["id"], self.owner, checks_complete=True, review=skill.last_review_result,
                                      plagiarism=skill.last_plagiarism_result, warning=getattr(skill, "retrieval_warning", ""))
        checkpoint = self.store.get(job["id"])
        if not checkpoint["article"].strip():
            raise ValueError("生成内容为空，未创建作品版本")
        self._stage(job, "saving")
        saved_path = self.exporter(checkpoint)
        self._check(job)
        self.store.commit_version(job["id"], self.owner, str(saved_path))

    def _export(self, job):
        author = resolve_under_base(self.root / "authors", self.root / "authors" / job["author"])
        output = author / "output"
        if not author.is_dir():
            raise FileNotFoundError("作家已归档，请恢复作家后再继续保存")
        output = resolve_under_base(author, output)
        if job["payload"].get("save_dir"):
            candidate = Path(job["payload"]["save_dir"])
            if not candidate.is_absolute():
                candidate = self.root / candidate
            output = resolve_under_base(output, candidate)
        output.mkdir(parents=True, exist_ok=True)
        path = output / (job["version_id"] + ".txt")
        atomic_write_text(path, job["article"])
        atomic_write_json(path.with_suffix(".review.json"), job["review"])
        atomic_write_json(path.with_suffix(".version.json"), {
            "version_id": job["version_id"], "article_id": job["article_id"], "job_id": job["id"],
            "kind": "draft" if job["kind"] == "write" else job["kind"], "author": job["author"],
            "topic": job["topic"], "article_path": str(path), "request": job["options"],
            "parent_version": job["payload"].get("parent_version", ""), "restored_from": job["payload"].get("restored_from"),
            "created_at": job["created_at"], "article_chars": len(job["article"]),
            "review_summary": _summarize_review(job["review"]), "plagiarism_summary": _summarize_plagiarism(job["plagiarism"]),
        })
        return path

    def _import(self, job):
        from .author_manager import create_author, get_author_info, _build_vector_index
        from .safety import safe_filename
        payload = job["payload"]
        staging = resolve_under_base(self.root / ".stylemuse" / "uploads", self.root / ".stylemuse" / "uploads" / payload["staging_id"])
        self._stage(job, "importing")
        author = create_author(job["author"], analyze=False, build_index=False)
        for file in payload["files"]:
            self._check(job)
            source = resolve_under_base(staging, staging / file["stored_name"])
            suffix = Path(file["filename"]).suffix.lower()
            target = author / ("epub" if suffix == ".epub" else "works")
            # Content hash avoids importing duplicate files after a manual retry.
            destination = target / safe_filename(file["sha256"][:16] + "_" + Path(file["filename"]).stem, suffix=suffix)
            if not destination.exists():
                atomic_copy(source, destination)
        self._stage(job, "analyzing")
        create_author(job["author"], analyze=True, build_index=False)
        self._stage(job, "indexing")
        _build_vector_index(job["author"], author)
        self._check(job)
        self.store.finish(job["id"], self.owner, "completed", result={"author": get_author_info(job["author"]), "kind": "import"})
        try:
            discard_staging(self.root, payload["staging_id"])
        except OSError:
            log.warning("Completed import staging could not be removed")
