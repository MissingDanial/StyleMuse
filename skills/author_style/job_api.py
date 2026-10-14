"""Task and history HTTP APIs; model work runs outside request threads."""
import difflib
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, Response
from .job_store import ConflictError, TERMINAL
from .safety import validate_simple_name, resolve_under_base, safe_filename
from .jobs import discard_staging


def create_blueprint(service_getter, normalize):
    bp = Blueprint("tasks", __name__)

    @bp.errorhandler(ConflictError)
    def conflict(error):
        return jsonify({"ok": False, "error": str(error)}), 409

    @bp.errorhandler(FileNotFoundError)
    def missing(error):
        return jsonify({"ok": False, "error": str(error)}), 404

    @bp.errorhandler(ValueError)
    def invalid(error):
        return jsonify({"ok": False, "error": str(error)}), 400

    def idempotency_key():
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9_:\-]{1,128}", key):
            raise ValueError("请提供有效 Idempotency-Key 提交编号")
        return key

    def number(key, default, minimum=0, maximum=1000000):
        try:
            result = int(request.args.get(key, default))
        except (ValueError, TypeError):
            raise ValueError(f"无效 {key}") from None
        if not minimum <= result <= maximum:
            raise ValueError(f"无效 {key}")
        return result

    @bp.route("/api/jobs", methods=["GET", "POST"])
    def jobs():
        service = service_getter()
        if request.method == "GET":
            return jsonify({"ok": True, "data": service.store.list_jobs(number("limit", 30, 1, 100))})
        key = idempotency_key()
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            raise ValueError("缺少有效任务数据")
        kind = raw.get("kind", "write")
        if not isinstance(kind, str) or kind not in {"write", "rewrite", "restore"}:
            raise ValueError("无效任务类型")
        if kind == "write":
            if any(key in raw for key in ("parent_version", "article_id", "restored_from", "version_id", "article", "review")):
                raise ValueError("初稿不能指定作品编号、父版本或已有正文")
            payload = normalize(data=raw)
        else:
            if not isinstance(raw.get("version_id"), str):
                raise ValueError("请选择作品版本")
            version = service.store.get_version(raw["version_id"])
            if raw.get("author") and raw["author"] != version["author"]:
                raise ConflictError("所选版本不属于该作家")
            options = version["request"]
            payload = {key: options[key] for key in ("tone", "length", "include_retrieval", "model", "temperature", "max_tokens") if options.get(key) is not None}
            payload.update({"author": version["author"], "topic": version["topic"], "save_dir": raw.get("save_dir", "")})
            payload = normalize(data=payload)
            payload["article_id"] = version["article_id"]
            payload["parent_version"] = version["version_id"]
            if kind == "restore":
                payload["restored_from"] = version["version_id"]
                payload["parent_version"] = service.store.get_article(version["article_id"])["latest_version_id"]
        if not (service.root / "authors" / payload["author"]).is_dir():
            raise FileNotFoundError("作家不存在或已归档")
        payload = {k: v for k, v in payload.items() if k in {"author", "topic", "tone", "length", "include_retrieval", "model", "temperature", "max_tokens", "save_dir", "article_id", "parent_version", "restored_from"}}
        job, created = service.store.submit(kind, payload, key)
        service.wake.set()
        return jsonify({"ok": True, "data": job}), 202 if created else 200

    @bp.route("/api/jobs/<job_id>")
    def get_job(job_id):
        return jsonify({"ok": True, "data": service_getter().store.get(job_id)})

    @bp.route("/api/jobs/by-key/<key>")
    def job_by_key(key):
        return jsonify({"ok": True, "data": service_getter().store.get_by_key(key)})

    @bp.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    def cancel_job(job_id):
        return jsonify({"ok": True, "data": service_getter().store.cancel(job_id)})

    @bp.route("/api/jobs/<job_id>/resume", methods=["POST"])
    def resume_job(job_id):
        service = service_getter()
        job = service.store.resume(job_id)
        service.wake.set()
        return jsonify({"ok": True, "data": job}), 202

    @bp.route("/api/jobs/<job_id>/events")
    def events(job_id):
        store = service_getter().store
        job = store.get(job_id)
        return jsonify({"ok": True, "data": {"events": store.events(job_id, number("after", 0)), "job": job}})

    @bp.route("/api/jobs/<job_id>/stream")
    def stream(job_id):
        store = service_getter().store
        store.get(job_id)
        try:
            cursor = int(request.headers.get("Last-Event-ID", request.args.get("after", "0")))
            if cursor < 0:
                raise ValueError
        except ValueError:
            raise ValueError("无效事件序号") from None
        def generate():
            position = cursor
            started = time.monotonic()
            while time.monotonic() - started < 45:
                updates = store.events(job_id, position)
                for event in updates:
                    position = event["sequence"]
                    yield f"id: {position}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if store.get(job_id)["status"] in TERMINAL and len(updates) < 300:
                    return
                yield ": heartbeat\n\n"
                time.sleep(1)
        return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})

    @bp.route("/api/import-jobs", methods=["POST"])
    def import_job():
        key = idempotency_key()
        name = validate_simple_name(request.form.get("name", ""), "作家名称")
        uploads = request.files.getlist("files")
        if not uploads:
            raise ValueError("请上传至少一个文件")
        if any(not file.filename or Path(file.filename).suffix.lower() not in {".txt", ".epub"} for file in uploads):
            raise ValueError("只支持 TXT 和 EPUB 文件")
        service = service_getter()
        staging_id = uuid.uuid4().hex
        staging = service.root / ".stylemuse" / "uploads" / staging_id
        staging.mkdir(parents=True)
        files = []
        for index, upload in enumerate(uploads):
            stored_name = f"{index}{Path(upload.filename).suffix.lower()}"
            path = staging / stored_name
            upload.save(path)
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({"filename": safe_filename(upload.filename), "stored_name": stored_name, "sha256": digest})
        try:
            job, created = service.store.submit("import", {"author":name, "topic":"", "staging_id":staging_id, "files":files}, key)
        except Exception:
            discard_staging(service.root, staging_id)
            raise
        if not created:
            discard_staging(service.root, staging_id)
        service.wake.set()
        return jsonify({"ok": True, "data": job}), 202 if created else 200

    @bp.route("/api/articles")
    def articles():
        author = request.args.get("author", "")
        if author:
            validate_simple_name(author, "作家名称")
        offset, limit = number("offset", 0), number("limit", 30, 1, 100)
        service = service_getter()
        service.store.import_legacy(service.root)
        items = service.store.list_articles(author, request.args.get("search", "")[:500], offset, limit)
        return jsonify({"ok": True, "data": {"items":items, "next_offset": offset + limit if len(items) == limit else None}})

    @bp.route("/api/articles/<article_id>")
    def article(article_id):
        store = service_getter().store
        return jsonify({"ok":True, "data": {**store.get_article(article_id), "versions":store.list_versions(article_id)}})

    @bp.route("/api/versions/<version_id>")
    def version(version_id):
        return jsonify({"ok":True, "data":service_getter().store.get_version(version_id)})

    @bp.route("/api/versions/<version_id>/reuse-review", methods=["POST"])
    def reuse_review(version_id):
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            raise ValueError("缺少复核数据")
        if not isinstance(raw.get("decision"), str):
            raise ValueError("无效复核结论")
        data = service_getter().store.save_reuse_review(version_id, raw.get("evidence_id"),
                                                       raw["decision"], raw.get("note", ""))
        return jsonify({"ok": True, "data": data})

    @bp.route("/api/articles/<article_id>/compare")
    def compare(article_id):
        store = service_getter().store
        left = store.get_version(request.args.get("left", ""))
        right = store.get_version(request.args.get("right", ""))
        if left["article_id"] != article_id or right["article_id"] != article_id:
            raise ConflictError("只能对比同一作品的版本")
        diff = "".join(difflib.unified_diff(left["article"].splitlines(keepends=True), right["article"].splitlines(keepends=True),
                                            fromfile=left["version_id"], tofile=right["version_id"]))
        return jsonify({"ok":True, "data":{"left":left, "right":right, "diff":diff}})

    return bp
