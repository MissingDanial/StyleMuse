"""SQLite is the source of truth for jobs, checkpoints and immutable versions."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .cache import fingerprint
from .artifacts import _summarize_review, _summarize_plagiarism
from .safety import resolve_under_base

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class ConflictError(ValueError):
    pass


class JobStopped(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, heartbeat REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                    payload_hash TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    author TEXT NOT NULL, topic TEXT NOT NULL, status TEXT NOT NULL,
                    stage TEXT NOT NULL, owner TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    article TEXT NOT NULL DEFAULT '', generation_complete INTEGER NOT NULL DEFAULT 0,
                    checks_complete INTEGER NOT NULL DEFAULT 0, review TEXT, plagiarism TEXT,
                    options TEXT, warning TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    result TEXT, article_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id), data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, sequence);
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY, author TEXT NOT NULL, topic TEXT NOT NULL,
                    latest_version_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id),
                    parent_version_id TEXT REFERENCES versions(id), kind TEXT NOT NULL,
                    restored_from TEXT, article TEXT NOT NULL, review TEXT, plagiarism TEXT,
                    options TEXT NOT NULL, saved_path TEXT NOT NULL, created_at TEXT NOT NULL,
                    legacy_path TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS versions_article ON versions(article_id, created_at);
                CREATE TABLE IF NOT EXISTS reuse_reviews (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT NOT NULL REFERENCES versions(id), evidence_id TEXT NOT NULL,
                    decision TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS reuse_reviews_version ON reuse_reviews(version_id, sequence);
            """)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db, job_id, value):
        db.execute("INSERT INTO events(job_id,data) VALUES (?,?)", (job_id, encode(value)))

    @staticmethod
    def _job(row):
        if row is None:
            raise FileNotFoundError("任务不存在")
        job = dict(row)
        for key in ("payload", "review", "plagiarism", "options", "result"):
            job[key] = json.loads(job[key]) if job[key] else None
        for key in ("cancel_requested", "generation_complete", "checks_complete"):
            job[key] = bool(job[key])
        job.pop("payload_hash", None)
        job.pop("owner", None)
        job["can_resume"] = (job["kind"] != "import" and job["generation_complete"]
                             and job["status"] in {"failed", "interrupted"})
        return job

    def get(self, job_id):
        with self.connection() as db:
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def get_by_key(self, key):
        with self.connection() as db:
            return self._job(db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone())

    def list_jobs(self, limit=30):
        with self.connection() as db:
            jobs = [self._job(row) for row in db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]
        for job in jobs:
            for key in ("article", "payload", "result", "review", "plagiarism", "options"):
                job.pop(key, None)
        return jobs

    def submit(self, kind, payload, key):
        hash_payload = {k: v for k, v in payload.items() if k != "staging_id"}
        if kind == "restore":
            # The head changes after restoring; retry identity is the selected
            # source version, not the subsequently updated latest version.
            hash_payload.pop("parent_version", None)
        digest = fingerprint({"kind": kind, "payload": hash_payload})
        with self.connection(write=True) as db:
            existing = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    raise ConflictError("同一提交编号不能用于不同内容")
                return self._job(existing), False
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 30:
                raise ConflictError("任务队列已满，请稍后提交")
            job_id, version_id = uuid.uuid4().hex, uuid.uuid4().hex
            article_id = payload.get("article_id") or uuid.uuid4().hex
            timestamp = now()
            db.execute("""INSERT INTO jobs(id,idempotency_key,payload_hash,kind,payload,author,topic,
                        status,stage,article_id,version_id,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,'queued','queued',?,?,?,?)""",
                       (job_id, key, digest, kind, encode(payload), payload["author"], payload.get("topic", ""),
                        article_id, version_id, timestamp, timestamp))
            self._event(db, job_id, {"type": "stage", "stage": "queued"})
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()), True

    def heartbeat(self, worker_id):
        with self.connection(write=True) as db:
            db.execute("INSERT INTO workers VALUES (?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat",
                       (worker_id, time.time()))

    def recover(self, lease_seconds=45):
        with self.connection(write=True) as db:
            stale = db.execute("""SELECT id FROM jobs WHERE status='running' AND (owner IS NULL OR owner NOT IN
                                (SELECT id FROM workers WHERE heartbeat>=?))""", (time.time() - lease_seconds,)).fetchall()
            for row in stale:
                db.execute("UPDATE jobs SET status='interrupted',stage='interrupted',owner=NULL,error=?,updated_at=? WHERE id=?",
                           ("服务中断；已保存草稿，不会自动再次生成", now(), row["id"]))
                self._event(db, row["id"], {"type": "stage", "stage": "interrupted"})
            return len(stale)

    def abandon_owner(self, owner):
        """A serial worker can have no running job between executions."""
        with self.connection(write=True) as db:
            rows = db.execute("SELECT id FROM jobs WHERE owner=? AND status='running'", (owner,)).fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET status='interrupted',stage='interrupted',owner=NULL,error=?,updated_at=? WHERE id=?",
                           ("上一阶段状态提交失败，草稿已保留，请确认后继续处理", now(), row["id"]))
                self._event(db, row["id"], {"type":"stage", "stage":"interrupted"})

    def claim(self, owner):
        with self.connection(write=True) as db:
            row = db.execute("""SELECT j.id FROM jobs j WHERE j.status='queued' AND NOT EXISTS
                              (SELECT 1 FROM jobs running WHERE running.status='running' AND running.author=j.author)
                              ORDER BY j.created_at LIMIT 1""").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',owner=?,updated_at=? WHERE id=?", (owner, now(), row["id"]))
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    @staticmethod
    def _require_running(db, job_id, owner):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["status"] != "running" or row["owner"] != owner or row["cancel_requested"]:
            raise JobStopped("任务已取消或不再由当前工作进程处理")
        return row

    def checkpoint(self, job_id, owner, **changes):
        allowed = {"stage", "article", "generation_complete", "checks_complete", "review", "plagiarism", "options", "warning"}
        if set(changes) - allowed:
            raise ValueError("invalid checkpoint fields")
        with self.connection(write=True) as db:
            self._require_running(db, job_id, owner)
            values = [encode(value) if key in {"review", "plagiarism", "options"} else value for key, value in changes.items()]
            assignments = ",".join(f"{key}=?" for key in changes)
            db.execute(f"UPDATE jobs SET {assignments},updated_at=? WHERE id=?", (*values, now(), job_id))
            if "stage" in changes:
                self._event(db, job_id, {"type": "stage", "stage": changes["stage"]})

    def append(self, job_id, owner, chunk):
        with self.connection(write=True) as db:
            self._require_running(db, job_id, owner)
            db.execute("UPDATE jobs SET article=article||?,updated_at=? WHERE id=?", (chunk, now(), job_id))
            self._event(db, job_id, {"type": "chunk", "content": chunk})

    def ensure_running(self, job_id, owner):
        with self.connection() as db:
            self._require_running(db, job_id, owner)

    def finish(self, job_id, owner, status, error="", result=None):
        if status not in TERMINAL:
            raise ValueError("invalid terminal job status")
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND status='running'", (job_id, owner)).fetchone()
            if row is None:
                return
            if row["cancel_requested"]:
                status, error = "cancelled", "任务已取消，草稿已保留"
            db.execute("UPDATE jobs SET status=?,stage=?,error=?,result=?,owner=NULL,updated_at=? WHERE id=?",
                       (status, status, error, encode(result) if result else None, now(), job_id))
            self._event(db, job_id, {"type": "done", **(result or {})} if status == "completed" else {"type": "stage", "stage": status})

    def cancel(self, job_id):
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise FileNotFoundError("任务不存在")
            if row["status"] not in TERMINAL:
                status = "cancelled" if row["status"] == "queued" else row["status"]
                stage = "cancelled" if status == "cancelled" else row["stage"]
                db.execute("UPDATE jobs SET cancel_requested=1,status=?,stage=?,updated_at=? WHERE id=?", (status, stage, now(), job_id))
                self._event(db, job_id, {"type": "stage", "stage": "cancelling" if status == "running" else status})
        return self.get(job_id)

    def resume(self, job_id):
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            job = self._job(row)
            if not job["can_resume"]:
                raise ConflictError("只有已生成完整正文的失败或中断任务可以继续处理")
            db.execute("UPDATE jobs SET status='queued',stage='queued',cancel_requested=0,owner=NULL,error='',updated_at=? WHERE id=?", (now(), job_id))
            self._event(db, job_id, {"type": "stage", "stage": "queued"})
        return self.get(job_id)

    def events(self, job_id, after=0):
        with self.connection() as db:
            return [{"sequence": row["sequence"], **json.loads(row["data"])} for row in db.execute(
                "SELECT * FROM events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT 300", (job_id, after))]

    @staticmethod
    def _version(db, row):
        if row is None:
            raise FileNotFoundError("作品版本不存在")
        version = dict(row)
        article = db.execute("SELECT * FROM articles WHERE id=?", (row["article_id"],)).fetchone()
        version.update({"version_id": version.pop("id"), "author": article["author"], "topic": article["topic"]})
        for key in ("review", "plagiarism", "options"):
            version[key] = json.loads(version[key]) if version[key] else None
        version["request"] = version.pop("options") or {}
        version["parent_version"] = version.pop("parent_version_id") or ""
        version["article_chars"] = len(version["article"])
        version["review_summary"] = _summarize_review(version["review"])
        version["plagiarism_summary"] = _summarize_plagiarism(version["plagiarism"])
        version.update(JobStore._reuse_review_state(db, version["version_id"], version["plagiarism"]))
        return version

    @staticmethod
    def _reuse_review_state(db, version_id, plagiarism):
        candidates = ((plagiarism or {}).get("semantic") or {}).get("candidates", [])
        valid_ids = {hit["evidence_id"] for hit in candidates if hit.get("evidence_id")}
        latest = {}
        for row in db.execute("SELECT * FROM reuse_reviews WHERE version_id=? ORDER BY sequence", (version_id,)):
            if row["evidence_id"] in valid_ids:
                latest[row["evidence_id"]] = dict(row)
        resolved = sum(row["decision"] != "uncertain" for row in latest.values())
        decisions = {row["decision"] for row in latest.values()}
        status = "pending" if resolved < len(valid_ids) else "reviewed"
        if "reuse" in decisions:
            status = "reuse_identified"
        semantic = (plagiarism or {}).get("semantic") or {}
        return {"reuse_reviews": list(latest.values()),
                "reuse_review_summary": {"status": status if valid_ids else "no_candidates", "resolved": resolved,
                                         "displayed_candidates": len(valid_ids), "scope": "displayed_candidates_only",
                                         "retrieval_truncated": bool(semantic.get("display_truncated") or semantic.get("scan_truncated"))}}

    def save_reuse_review(self, version_id, evidence_id, decision, note=""):
        if not isinstance(decision, str) or decision not in {"reuse", "independent", "quotation", "uncertain"}:
            raise ValueError("无效复核结论")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("复核依据必须为不超过 2000 字的文字")
        with self.connection(write=True) as db:
            version = self._version(db, db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone())
            valid_ids = {hit.get("evidence_id") for hit in (version["plagiarism"] or {}).get("semantic", {}).get("candidates", [])}
            if not isinstance(evidence_id, str) or not evidence_id or evidence_id not in valid_ids:
                raise ConflictError("该证据不属于此作品版本")
            db.execute("INSERT INTO reuse_reviews(version_id,evidence_id,decision,note,created_at) VALUES (?,?,?,?,?)",
                       (version_id, evidence_id, decision, note.strip(), now()))
            return self._reuse_review_state(db, version_id, version["plagiarism"])

    def get_version(self, version_id):
        with self.connection() as db:
            return self._version(db, db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone())

    def list_versions(self, article_id):
        with self.connection() as db:
            if not db.execute("SELECT id FROM articles WHERE id=?", (article_id,)).fetchone():
                raise FileNotFoundError("作品不存在")
            return [self._version(db, row) for row in db.execute(
                "SELECT * FROM versions WHERE article_id=? ORDER BY created_at,id", (article_id,))]

    def get_article(self, article_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
            if row is None:
                raise FileNotFoundError("作品不存在")
            return dict(row)

    def list_articles(self, author="", search="", offset=0, limit=30):
        with self.connection() as db:
            rows = db.execute("""SELECT a.*,v.kind,v.review,length(v.article) AS article_chars,
                                (SELECT count(*) FROM versions WHERE article_id=a.id) AS version_count
                                FROM articles a JOIN versions v ON v.id=a.latest_version_id
                                WHERE (?='' OR a.author=?) AND instr(a.topic,?)>0
                                ORDER BY a.updated_at DESC,a.id LIMIT ? OFFSET ?""",
                              (author, author, search, limit, offset)).fetchall()
            result = [dict(row) for row in rows]
        for article in result:
            article["review_summary"] = _summarize_review(json.loads(article.pop("review") or "null"))
        return result

    def commit_version(self, job_id, owner, saved_path):
        with self.connection(write=True) as db:
            row = self._require_running(db, job_id, owner)
            job = self._job(row)
            if not job["generation_complete"] or not job["checks_complete"]:
                raise ConflictError("正文和检查未完成，不能提交作品版本")
            parent = job["payload"].get("parent_version") or None
            if parent:
                parent_row = db.execute("SELECT article_id FROM versions WHERE id=?", (parent,)).fetchone()
                if parent_row is None or parent_row["article_id"] != job["article_id"]:
                    raise ConflictError("父版本不属于当前作品")
            timestamp = now()
            existing_article = db.execute("SELECT author FROM articles WHERE id=?", (job["article_id"],)).fetchone()
            if existing_article and existing_article["author"] != job["author"]:
                raise ConflictError("作品不属于当前作家")
            db.execute("INSERT OR IGNORE INTO articles(id,author,topic,created_at,updated_at) VALUES (?,?,?,?,?)",
                       (job["article_id"], job["author"], job["topic"], timestamp, timestamp))
            db.execute("""INSERT INTO versions(id,article_id,parent_version_id,kind,restored_from,article,review,
                        plagiarism,options,saved_path,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (job["version_id"], job["article_id"], parent, "draft" if job["kind"] == "write" else job["kind"],
                        job["payload"].get("restored_from"), job["article"], row["review"], row["plagiarism"],
                        row["options"] or "{}", saved_path, timestamp))
            db.execute("UPDATE articles SET latest_version_id=?,updated_at=? WHERE id=?", (job["version_id"], timestamp, job["article_id"]))
            version = self._version(db, db.execute("SELECT * FROM versions WHERE id=?", (job["version_id"],)).fetchone())
            result = {"article": job["article"], "author": job["author"], "topic": job["topic"], "version": version,
                      "review": job["review"], "plagiarism": job["plagiarism"], "saved_path": saved_path,
                      "retrieval_warning": job["warning"]}
            db.execute("UPDATE jobs SET status='completed',stage='completed',result=?,owner=NULL,error='',updated_at=? WHERE id=?",
                       (encode(result), timestamp, job_id))
            self._event(db, job_id, {"type": "done", **result})
            return result

    def has_active_author(self, author):
        with self.connection() as db:
            return db.execute("SELECT 1 FROM jobs WHERE author=? AND status IN ('queued','running') LIMIT 1", (author,)).fetchone() is not None

    def import_legacy(self, root):
        """Index existing CLI/Web exports without changing their original files."""
        root = Path(root).resolve()
        with self.connection() as db:
            known = {row[0] for row in db.execute("SELECT legacy_path FROM versions WHERE legacy_path IS NOT NULL")}
        candidates = {}
        for author in (root / "authors").glob("*"):
            if not author.is_dir() or author.name.startswith("."):
                continue
            output = author / "output"
            for path in output.rglob("*.txt"):
                try:
                    path = resolve_under_base(output, path)
                    relative = path.relative_to(root).as_posix()
                    if relative in known:
                        continue
                    metadata_path = path.with_suffix(".version.json")
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
                    if not isinstance(metadata, dict):
                        metadata = {}
                    if metadata.get("job_id"):
                        # Uncommitted job exports are not completed legacy versions.
                        continue
                    review_path = path.with_suffix(".review.json")
                    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else None
                    review = review if isinstance(review, dict) else None
                    created = metadata.get("created_at", "")
                    try:
                        created = datetime.fromisoformat(created).astimezone(timezone.utc).isoformat()
                    except (ValueError, TypeError):
                        created = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    candidates[relative] = {"path":path, "id":uuid.uuid5(uuid.NAMESPACE_URL, "stylemuse:" + relative).hex,
                        "author":author.name, "topic":str(metadata.get("topic") or path.stem), "body":path.read_text(encoding="utf-8"),
                        "review":review, "plagiarism":metadata.get("plagiarism_summary") if isinstance(metadata.get("plagiarism_summary"), dict) else None,
                        "options":metadata.get("request") if isinstance(metadata.get("request"), dict) else {},
                        "parent":str(metadata.get("parent_version") or ""), "created_at":created}
                except (OSError, ValueError, TypeError):
                    continue
        imported = 0
        with self.connection(write=True) as db:
            def insert(relative, visiting):
                nonlocal imported
                candidate = candidates[relative]
                existing = db.execute("SELECT article_id FROM versions WHERE legacy_path=?", (relative,)).fetchone()
                if existing:
                    return existing["article_id"], candidate["id"]
                parent_id = None
                parent_path = candidate["path"].parent / (Path(candidate["parent"]).stem + ".txt")
                parent_relative = parent_path.relative_to(root).as_posix()
                parent = db.execute("SELECT id,article_id FROM versions WHERE legacy_path=?", (parent_relative,)).fetchone()
                if parent:
                    article_id, parent_id = parent["article_id"], parent["id"]
                elif candidate["parent"] and parent_relative != relative and parent_relative in candidates and parent_relative not in visiting:
                    article_id, parent_id = insert(parent_relative, visiting | {relative})
                else:
                    article_id = uuid.uuid5(uuid.NAMESPACE_URL, "stylemuse article:" + relative).hex
                db.execute("INSERT OR IGNORE INTO articles(id,author,topic,created_at,updated_at) VALUES (?,?,?,?,?)",
                           (article_id, candidate["author"], candidate["topic"], candidate["created_at"], candidate["created_at"]))
                db.execute("""INSERT INTO versions(id,article_id,parent_version_id,kind,article,review,plagiarism,
                            options,saved_path,created_at,legacy_path) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (candidate["id"], article_id, parent_id, "rewrite" if parent_id else "draft", candidate["body"],
                            encode(candidate["review"]), encode(candidate["plagiarism"]), encode(candidate["options"]),
                            str(candidate["path"]), candidate["created_at"], relative))
                db.execute("UPDATE articles SET latest_version_id=?,updated_at=? WHERE id=? AND updated_at<=?",
                           (candidate["id"], candidate["created_at"], article_id, candidate["created_at"]))
                imported += 1
                return article_id, candidate["id"]
            for relative in sorted(candidates, key=lambda key: candidates[key]["created_at"]):
                insert(relative, set())
        return imported
