"""
通用作家风格仿写 Skill - Web 服务

启动方式:
    python app.py

访问地址:
    http://localhost:5000
"""

import os
import sys
import json
import subprocess
import traceback
import hashlib
import hmac
import math
import secrets
import re
from urllib.parse import urlsplit
from threading import RLock
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from flask import Flask, request, jsonify, send_from_directory, send_file, Response, session
from dotenv import dotenv_values
from werkzeug.exceptions import HTTPException
from skills.author_style import (
    AuthorStyleSkill,
    create_author,
    list_authors,
    delete_author,
    get_author_info,
)
from skills.author_style.safety import resolve_under_base, safe_filename, unique_path, validate_simple_name, write_unique_text
from skills.author_style.cache import atomic_write_json, atomic_write_text
from skills.author_style.artifacts import save_review as _save_review, save_version_metadata as _save_version_metadata

app = Flask(__name__, static_folder="static")
app.config.update(
    MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    MAX_FORM_PARTS=100,
    ACCESS_TOKEN=os.getenv("STYLEMUSE_ACCESS_TOKEN", ""),
    SECRET_KEY=os.getenv("STYLEMUSE_SESSION_SECRET") or (
        hashlib.sha256(("StyleMuse session:" + os.getenv("STYLEMUSE_ACCESS_TOKEN", "")).encode()).hexdigest()
        if os.getenv("STYLEMUSE_ACCESS_TOKEN") else secrets.token_hex(32)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.getenv("STYLEMUSE_HTTPS", "0") == "1",
    JOB_WORKER_ENABLED=os.getenv("STYLEMUSE_EXTERNAL_WORKER", "0") != "1",
)
_config_lock = RLock()
_job_lock = RLock()

ENV_FILE = project_root / ".env"
MODELS_FILE = project_root / "models.json"
SUPPORTED_UPLOAD_SUFFIXES = {".txt", ".epub"}
ALLOWED_MODEL_PROVIDERS = {"openai", "anthropic"}
MAX_CUSTOM_MODELS = 50
MAX_MODEL_FIELD_LENGTH = 300


def _job_service():
    from skills.author_style.jobs import JobService
    key = str(project_root.resolve())
    with _job_lock:
        services = app.extensions.setdefault("job_services", {})
        if key not in services:
            services[key] = JobService(project_root, AuthorStyleSkill, app.config.get("JOB_DATABASE"))
        service = services[key]
        if app.config.get("JOB_WORKER_ENABLED", True):
            service.start()
        return service


def _has_access():
    token = app.config["ACCESS_TOKEN"]
    if not token:
        return request.remote_addr in {"127.0.0.1", "::1"}
    supplied = request.headers.get("Authorization", "")
    return (hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()) or
            hmac.compare_digest(session.get("access", ""), hashlib.sha256(token.encode()).hexdigest()))


@app.before_request
def enforce_api_access():
    if not request.path.startswith("/api/"):
        return
    if not app.config["ACCESS_TOKEN"] and urlsplit(request.host_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        return jsonify({"ok": False, "error": "本机模式不接受该 Host"}), 403
    origin = request.headers.get("Origin")
    if origin and request.method not in {"GET", "HEAD"} and urlsplit(origin).netloc != request.host:
        return jsonify({"ok": False, "error": "不接受跨站写入请求"}), 403
    if request.content_length and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({"ok": False, "error": "请求超过 32 MB 上传限制"}), 413
    if request.path not in {"/api/access", "/api/session"} and not _has_access():
        return jsonify({"ok": False, "error": "需要访问密码；共享部署请配置 STYLEMUSE_ACCESS_TOKEN"}), 401
    if request.mimetype == "multipart/form-data" and sum(len(values) for _, values in request.files.lists()) > 20:
        return jsonify({"ok": False, "error": "一次最多上传 20 个文件"}), 400


@app.errorhandler(HTTPException)
def http_error(error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": error.description}), error.code
    return error


@app.after_request
def response_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.route("/api/access")
def api_access():
    return jsonify({"ok": True, "data": {"required": bool(app.config["ACCESS_TOKEN"]), "authorized": _has_access()}})


@app.route("/api/session", methods=["POST", "DELETE"])
def api_session():
    if request.method == "DELETE":
        session.clear()
        return jsonify({"ok": True})
    data = request.get_json(silent=True)
    token = app.config["ACCESS_TOKEN"]
    if not token or not isinstance(data, dict) or not isinstance(data.get("password"), str):
        return jsonify({"ok": False, "error": "访问密码无效"}), 401
    if not hmac.compare_digest(data["password"].encode(), token.encode()):
        return jsonify({"ok": False, "error": "访问密码无效"}), 401
    session.clear()
    session["access"] = hashlib.sha256(token.encode()).hexdigest()
    return jsonify({"ok": True})


# ============================================================================
# 页面路由
# ============================================================================

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ============================================================================
# 作家管理 API
# ============================================================================

@app.route("/api/authors", methods=["GET"])
def api_list_authors():
    try:
        authors = list_authors()
        return jsonify({"ok": True, "data": authors})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/author/<name>", methods=["GET"])
def api_get_author(name):
    try:
        info = get_author_info(name)
        if info is None:
            return jsonify({"ok": False, "error": f"作家 '{name}' 不存在"}), 404
        return jsonify({"ok": True, "data": info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/author", methods=["POST"])
def api_create_author():
    try:
        content_type = request.content_type or ""

        if "multipart/form-data" in content_type:
            name = request.form.get("name", "").strip()
            if not name:
                return jsonify({"ok": False, "error": "缺少作家名称"}), 400
            if _job_service().store.has_active_author(name):
                return jsonify({"ok": False, "error": "该作家有任务正在处理，请稍后更新语料"}), 409

            files = request.files.getlist("files")
            if not files:
                return jsonify({"ok": False, "error": "请上传至少一个文件"}), 400

            upload_files = []
            for f in files:
                if not f.filename:
                    continue
                suffix = Path(f.filename).suffix.lower()
                if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
                    return jsonify({"ok": False, "error": f"不支持的文件类型: {suffix}"}), 400
                upload_files.append((f, suffix))

            if not upload_files:
                return jsonify({"ok": False, "error": "没有可保存的上传文件"}), 400

            author_dir = create_author(name=name, source_path=None, analyze=False, build_index=False)
            for f, suffix in upload_files:
                target_dir = author_dir / ("epub" if suffix == ".epub" else "works")
                target_dir.mkdir(parents=True, exist_ok=True)
                filename = unique_path(target_dir, safe_filename(Path(f.filename).stem, "upload", suffix))
                f.save(filename)

            create_author(name=name, source_path=None, analyze=True, build_index=True)
        else:
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                return jsonify({"ok": False, "error": "缺少请求数据"}), 400
            name = data.get("name", "").strip()
            if not name:
                return jsonify({"ok": False, "error": "缺少作家名称"}), 400
            if _job_service().store.has_active_author(name):
                return jsonify({"ok": False, "error": "该作家有任务正在处理，请稍后更新语料"}), 409
            if data.get("source_path"):
                return jsonify({"ok": False, "error": "Web API 不接受 source_path，请使用文件上传"}), 400
            create_author(name=name, source_path=None)

        info = get_author_info(name)
        return jsonify({"ok": True, "data": info})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/author/<name>", methods=["DELETE"])
def api_delete_author(name):
    try:
        if _job_service().store.has_active_author(name):
            return jsonify({"ok": False, "error": "该作家仍有任务，请先取消或等待完成"}), 409
        data = request.get_json(silent=True) or {}
        if data.get("confirm") is not True:
            return jsonify({"ok": False, "error": "删除前必须确认 confirm=true"}), 400
        success = delete_author(name, confirm=False)
        return jsonify({"ok": success})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================================
# 写作 API
# ============================================================================

@app.route("/api/write", methods=["POST"])
def api_write():
    try:
        data = _generation_request()

        author = data.get("author", "").strip()
        topic = data.get("topic", "").strip()
        if not author:
            return jsonify({"ok": False, "error": "请选择作家"}), 400
        if not topic:
            return jsonify({"ok": False, "error": "请输入写作主题"}), 400

        kwargs = {}
        if data.get("model"):
            kwargs["model"] = data["model"]
        if data.get("temperature") is not None:
            kwargs["temperature"] = float(data["temperature"])
        if data.get("max_tokens"):
            kwargs["max_tokens"] = int(data["max_tokens"])

        skill = AuthorStyleSkill(author, **kwargs)
        article = skill.write(
            topic=topic,
            tone=data.get("tone", "default"),
            length=data.get("length", "medium"),
            include_retrieval=data.get("include_retrieval", True),
        )

        # 保存到指定位置
        filename = _save_article(author, topic, article, data.get("save_dir", ""))
        saved_path = str(filename)
        review_path = _save_review(filename, skill.last_review_result)
        version_path = _save_version_metadata(
            filename,
            kind="draft",
            author=author,
            topic=topic,
            article=article,
            review=skill.last_review_result,
            plagiarism=skill.last_plagiarism_result,
            parent_version=data.get("parent_version", ""),
            request_options=_request_generation_options(data, skill),
        )
        version = _load_json_file(version_path)

        return jsonify({
            "ok": True,
            "data": {
                "article": article,
                "topic": topic,
                "author": author,
                "saved_path": saved_path,
                "review_path": str(review_path) if review_path else "",
                "version_path": str(version_path),
                "version": version,
                "plagiarism": skill.last_plagiarism_result,
                "review": skill.last_review_result,
                "retrieval_warning": getattr(skill, "retrieval_warning", ""),
            },
        })

    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/write-stream", methods=["POST"])
def api_write_stream():
    """SSE 流式写作接口"""
    try:
        data = _generation_request()

        author = data.get("author", "").strip()
        topic = data.get("topic", "").strip()
        if not author:
            return jsonify({"ok": False, "error": "请选择作家"}), 400
        if not topic:
            return jsonify({"ok": False, "error": "请输入写作主题"}), 400

        kwargs = {}
        if data.get("model"):
            kwargs["model"] = data["model"]
        if data.get("temperature") is not None:
            kwargs["temperature"] = float(data["temperature"])
        if data.get("max_tokens"):
            kwargs["max_tokens"] = int(data["max_tokens"])

        skill = AuthorStyleSkill(author, **kwargs)

        tone = data.get("tone", "default")
        length = data.get("length", "medium")
        include_retrieval = data.get("include_retrieval", True)
        save_dir = data.get("save_dir", "").strip()

        def generate():
            article_chunks = []
            try:
                yield f"data: {json.dumps({'type': 'stage', 'stage': 'generating'}, ensure_ascii=False)}\n\n"
                for chunk in skill.write_stream(
                    topic=topic,
                    tone=tone,
                    length=length,
                    include_retrieval=include_retrieval,
                    run_checks=False,
                ):
                    article_chunks.append(chunk)
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"

                # 保存文章
                article = "".join(article_chunks)
                yield f"data: {json.dumps({'type': 'stage', 'stage': 'checking'}, ensure_ascii=False)}\n\n"
                skill._run_post_generation_checks(article, topic, tone, length)
                yield f"data: {json.dumps({'type': 'stage', 'stage': 'saving'}, ensure_ascii=False)}\n\n"
                filename = _save_article(author, topic, article, save_dir)
                saved_path = str(filename)
                review_path = _save_review(filename, skill.last_review_result)
                version_path = _save_version_metadata(
                    filename,
                    kind="draft",
                    author=author,
                    topic=topic,
                    article=article,
                    review=skill.last_review_result,
                    plagiarism=skill.last_plagiarism_result,
                    parent_version=data.get("parent_version", ""),
                    request_options=_request_generation_options(data, skill),
                )
                version = _load_json_file(version_path)

                yield f"data: {json.dumps({'type': 'done', 'article': article, 'topic': topic, 'author': author, 'saved_path': saved_path, 'review_path': str(review_path) if review_path else '', 'version_path': str(version_path), 'version': version, 'plagiarism': skill.last_plagiarism_result, 'review': skill.last_review_result, 'retrieval_warning': getattr(skill, 'retrieval_warning', '')}, ensure_ascii=False)}\n\n"
            except Exception as e:
                traceback.print_exc()
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/rewrite", methods=["POST"])
def api_rewrite():
    """Rewrite an article using the latest review feedback."""
    try:
        data = _generation_request(rewrite=True)

        author = data.get("author", "").strip()
        topic = data.get("topic", "").strip()
        article = data.get("article", "").strip()
        review = data.get("review") or {}
        if not author:
            return jsonify({"ok": False, "error": "请选择作家"}), 400
        if not topic:
            return jsonify({"ok": False, "error": "请输入写作主题"}), 400
        if not article:
            return jsonify({"ok": False, "error": "缺少待重写文章"}), 400
        if not isinstance(review, dict):
            return jsonify({"ok": False, "error": "review must be an object"}), 400

        kwargs = {}
        if data.get("model"):
            kwargs["model"] = data["model"]
        if data.get("temperature") is not None:
            kwargs["temperature"] = float(data["temperature"])
        if data.get("max_tokens"):
            kwargs["max_tokens"] = int(data["max_tokens"])

        skill = AuthorStyleSkill(author, **kwargs)
        rewritten = skill.rewrite(
            original_article=article,
            review=review,
            topic=topic,
            tone=data.get("tone", "default"),
            length=data.get("length", "medium"),
            include_retrieval=data.get("include_retrieval", True),
        )

        filename = _save_article(author, f"{topic}_rewrite", rewritten, data.get("save_dir", ""))
        saved_path = str(filename)
        review_path = _save_review(filename, skill.last_review_result)
        version_path = _save_version_metadata(
            filename,
            kind="rewrite",
            author=author,
            topic=topic,
            article=rewritten,
            review=skill.last_review_result,
            plagiarism=skill.last_plagiarism_result,
            parent_version=data.get("parent_version", ""),
            request_options=_request_generation_options(data, skill),
        )
        version = _load_json_file(version_path)

        return jsonify({
            "ok": True,
            "data": {
                "article": rewritten,
                "topic": topic,
                "author": author,
                "saved_path": saved_path,
                "review_path": str(review_path) if review_path else "",
                "version_path": str(version_path),
                "version": version,
                "plagiarism": skill.last_plagiarism_result,
                "review": skill.last_review_result,
                "retrieval_warning": getattr(skill, "retrieval_warning", ""),
            },
        })

    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    """下载文章为 txt 文件"""
    try:
        data = request.get_json()
        content = data.get("content", "")
        filename = safe_filename(data.get("filename", "article"), default="article", suffix=".txt")

        import io
        buf = io.BytesIO(content.encode("utf-8"))
        buf.seek(0)

        return send_file(
            buf,
            as_attachment=True,
            download_name=filename,
            mimetype="text/plain; charset=utf-8",
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/browse-folder", methods=["GET"])
def api_browse_folder():
    """打开系统文件夹选择对话框，返回选中的路径"""
    try:
        if app.config["ACCESS_TOKEN"] or request.remote_addr not in {"127.0.0.1", "::1"}:
            return jsonify({"ok": False, "error": "共享模式请使用下载按钮保存文章"}), 400
        # 用子进程调用 tkinter，避免 Flask 多线程冲突
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import tkinter as tk; from tkinter import filedialog; "
                "root=tk.Tk(); root.withdraw(); root.attributes('-topmost',True); "
                "f=filedialog.askdirectory(title='选择保存位置'); root.destroy(); "
                "print(f)"
            ],
            capture_output=True, text=True, timeout=120,
        )
        folder = result.stdout.strip()
        if folder:
            return jsonify({"ok": True, "data": {"path": folder}})
        return jsonify({"ok": False, "error": "未选择"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================================
# 模型配置 API
# ============================================================================

@app.route("/api/config", methods=["GET"])
def api_get_config():
    """读取 .env 配置（密钥脱敏）"""
    env_vars = _read_env_file()
    return jsonify({
        "ok": True,
        "data": {
            "LLM_PROVIDER": env_vars.get("LLM_PROVIDER", ""),
            "LLM_MODEL": env_vars.get("LLM_MODEL", ""),
            "LLM_BASE_URL": env_vars.get("LLM_BASE_URL", ""),
            "LLM_API_KEY": _mask_key(env_vars.get("LLM_API_KEY", "")),
            "LLM_API_KEY_SET": bool(env_vars.get("LLM_API_KEY", "")),
            "EMBEDDING_PROVIDER": env_vars.get("EMBEDDING_PROVIDER", ""),
            "EMBEDDING_MODEL": env_vars.get("EMBEDDING_MODEL", ""),
            "EMBEDDING_BASE_URL": env_vars.get("EMBEDDING_BASE_URL", ""),
            "EMBEDDING_API_KEY": _mask_key(env_vars.get("EMBEDDING_API_KEY", "")),
            "EMBEDDING_API_KEY_SET": bool(env_vars.get("EMBEDDING_API_KEY", "")),
        },
    })


@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存 .env 配置"""
    try:
        data = request.get_json(silent=True)
        _validate_config_fields(data)

        with _config_lock:
            env_vars = _read_env_file()
            for key, value in data.items():
                if value == "__CLEAR__":
                    env_vars[key] = ""
                elif value and value != "__KEEP__":
                    env_vars[key] = value
            _write_env_file(env_vars)

        return jsonify({"ok": True, "message": "配置已保存，需要重启服务生效"})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except PermissionError:
        return jsonify({"ok": False, "error": "配置文件只读，请在宿主机修改配置并重启服务"}), 409
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================================
# 自定义模型列表 API
# ============================================================================

@app.route("/api/models", methods=["GET"])
def api_get_models():
    """获取用户自定义模型列表"""
    models = _load_models()
    return jsonify({"ok": True, "data": models})


@app.route("/api/models", methods=["POST"])
def api_save_models():
    """保存用户自定义模型列表"""
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "invalid request body"}), 400
        models = _validate_models(data.get("models", []))
        _save_models(models)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================================
# 工具函数
# ============================================================================

def _validate_url(value: str):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("模型地址必须为有效的 HTTP 或 HTTPS URL")


def _validate_config_fields(data):
    allowed = {"LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY", "EMBEDDING_PROVIDER",
               "EMBEDDING_MODEL", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL"}
    if not isinstance(data, dict) or not data:
        raise ValueError("缺少有效配置数据")
    if set(data) - allowed:
        raise ValueError("包含不支持的配置字段")
    for key, value in data.items():
        if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError(f"{key} 必须为不含控制字符的字符串")
        if value in {"", "__CLEAR__", "__KEEP__"}:
            if value == "__CLEAR__" and key in {"LLM_PROVIDER", "EMBEDDING_PROVIDER", "LLM_MODEL", "EMBEDDING_MODEL"}:
                raise ValueError(f"{key} 不能清空")
            continue
        if key == "LLM_PROVIDER" and value not in ALLOWED_MODEL_PROVIDERS:
            raise ValueError("不支持的 LLM provider")
        if key == "EMBEDDING_PROVIDER" and value not in {"dashscope", "openai", "local"}:
            raise ValueError("不支持的 Embedding provider")
        if key.endswith("BASE_URL"):
            _validate_url(value)


def _generation_request(rewrite=False, data=None):
    data = request.get_json(silent=True) if data is None else dict(data)
    if not isinstance(data, dict) or not data:
        raise ValueError("缺少有效请求数据")
    for key, maximum in {"author": 120, "topic": 500, "model": 300, "save_dir": 1024}.items():
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{key} 长度或类型无效")
        data[key] = value.strip()
    validate_simple_name(data["author"], "作家名称")
    if not data["topic"]:
        raise ValueError("请输入写作主题")
    if not isinstance(data.get("length", "medium"), str) or data.get("length", "medium") not in {"short", "medium", "long"}:
        raise ValueError("无效篇幅档位")
    if not isinstance(data.get("tone", "default"), str) or len(data.get("tone", "default")) > 200:
        raise ValueError("无效语气")
    if type(data.get("include_retrieval", True)) is not bool:
        raise ValueError("include_retrieval 必须为布尔值")
    for key in ("temperature", "max_tokens"):
        value = data.get(key)
        if value is not None:
            if isinstance(value, bool):
                raise ValueError(f"{key} 必须为数字")
            try:
                number = float(value)
            except (ValueError, TypeError):
                raise ValueError(f"{key} 必须为数字") from None
            if not math.isfinite(number):
                raise ValueError(f"{key} 必须为有限数字")
            if key == "temperature" and not 0 <= number <= 2:
                raise ValueError("temperature 必须介于 0 和 2")
            if key == "max_tokens" and (not number.is_integer() or not 1 <= number <= 16000):
                raise ValueError("max_tokens 必须为 1 至 16000 的整数")
            data[key] = int(number) if key == "max_tokens" else number
    if rewrite and (not isinstance(data.get("article"), str) or not data["article"].strip() or len(data["article"]) > 50000):
        raise ValueError("缺少有效待重写文章或文章超过 50000 字")
    if rewrite and not isinstance(data.get("review", {}), dict):
        raise ValueError("review must be an object")
    _resolve_save_dir(data["save_dir"], data["author"], create=False)
    return data

def _read_env_file() -> dict:
    """读取 .env 文件为字典"""
    if not ENV_FILE.exists():
        return {}
    return dict(dotenv_values(ENV_FILE, interpolate=False))


def _write_env_file(env: dict):
    """将字典写入 .env 文件"""
    lines = []
    env = dict(env)
    def assignment(key, value):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("配置键值无效")
        # Quote values so spaces, #, quotes and backslashes round-trip through dotenv.
        quoted = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"{key}='{quoted}'"
    # 保持原有顺序
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in env:
                    lines.append(assignment(key, env.pop(key)))
                else:
                    lines.append(line)
            else:
                lines.append(line)

    # 追加新增的键
    for key, value in env.items():
        lines.append(assignment(key, value))

    atomic_write_text(ENV_FILE, "\n".join(lines) + "\n")


def _mask_key(key: str) -> str:
    """密钥脱敏"""
    if not key or len(key) <= 8:
        return "****" if key else ""
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _load_models() -> list:
    """加载用户自定义模型列表"""
    if MODELS_FILE.exists():
        try:
            return json.loads(MODELS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 默认列表
    return [
        {"provider": "openai", "name": "deepseek-flash", "base_url": "https://api.deepseek.com/v1", "label": "DeepSeek Flash"},
        {"provider": "openai", "name": "deepseek-v4-pro", "base_url": "https://api.deepseek.com/v1", "label": "DeepSeek Pro"},
        {"provider": "openai", "name": "qwen-turbo", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "label": "通义千问"},
        {"provider": "openai", "name": "glm-4-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "label": "智谱 GLM"},
        {"provider": "anthropic", "name": "MiniMax-M2.7", "base_url": "https://api.minimaxi.com/anthropic", "label": "MiniMax"},
        {"provider": "openai", "name": "gpt-4o", "base_url": "", "label": "OpenAI"},
    ]


def _save_models(models: list):
    """保存用户自定义模型列表"""
    atomic_write_json(MODELS_FILE, models)


def _validate_models(models: list) -> list:
    """Validate and normalize custom model entries before persisting them."""
    if not isinstance(models, list):
        raise ValueError("models must be a list")
    if len(models) > MAX_CUSTOM_MODELS:
        raise ValueError(f"models cannot contain more than {MAX_CUSTOM_MODELS} entries")

    cleaned = []
    for index, model in enumerate(models, start=1):
        if not isinstance(model, dict):
            raise ValueError(f"model #{index} must be an object")

        provider = str(model.get("provider", "openai")).strip().lower()
        name = str(model.get("name", "")).strip()
        base_url = str(model.get("base_url", "")).strip()
        label = str(model.get("label", "")).strip() or name

        if provider not in ALLOWED_MODEL_PROVIDERS:
            raise ValueError(f"unsupported provider for model #{index}: {provider}")
        if not name:
            raise ValueError(f"model #{index} is missing name")
        for field_name, value in {
            "name": name,
            "base_url": base_url,
            "label": label,
        }.items():
            if len(value) > MAX_MODEL_FIELD_LENGTH:
                raise ValueError(f"model #{index} {field_name} is too long")
            if re.search(r"[\x00-\x1f\x7f]", value):
                raise ValueError(f"model #{index} {field_name} contains control characters")
        if base_url:
            _validate_url(base_url)

        cleaned.append({
            "provider": provider,
            "name": name,
            "base_url": base_url,
            "label": label,
        })
    return cleaned


def _resolve_save_dir(save_dir: str, author: str, create: bool = True) -> Path:
    """Resolve the requested save directory within the project workspace."""
    output_base = project_root / "authors" / validate_simple_name(author, "作家名称") / "output"
    output_base = resolve_under_base(project_root / "authors", output_base, "保存位置")
    if create:
        output_base.mkdir(parents=True, exist_ok=True)
    if save_dir and save_dir.strip():
        candidate = Path(save_dir.strip())
        if not candidate.is_absolute():
            candidate = project_root / candidate
        target = resolve_under_base(output_base, candidate, "保存位置")
    else:
        target = resolve_under_base(output_base, output_base, "保存位置")
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def _save_article(author: str, topic: str, article: str, save_dir: str = "") -> Path:
    """Save a generated article using a sanitized topic filename."""
    target_dir = _resolve_save_dir(save_dir, author)
    return write_unique_text(target_dir, safe_filename(topic, default="article", suffix=".txt"), article)


def _request_generation_options(data: dict, skill=None) -> dict:
    """Return non-secret generation options suitable for version metadata."""
    config = getattr(skill, "config", {}) or {}
    return {
        "tone": data.get("tone", "default"),
        "length": data.get("length", "medium"),
        "include_retrieval": data.get("include_retrieval", True),
        "model": config.get("llm_model", data.get("model", "")),
        "provider": config.get("llm_provider", ""),
        "temperature": config.get("temperature", data.get("temperature")),
        "max_tokens": config.get("max_tokens", data.get("max_tokens")),
    }


def _load_json_file(path: Path | None) -> dict:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


from skills.author_style.job_api import create_blueprint
app.register_blueprint(create_blueprint(_job_service, _generation_request))


def create_app():
    """Production factory starts the durable queue without an incoming request."""
    _job_service()
    return app


if __name__ == "__main__":
    print("=" * 50)
    print("通用作家风格仿写 Skill")
    print("访问地址: http://localhost:5000")
    print("=" * 50)
    if os.getenv("FLASK_HOST", "127.0.0.1") not in {"127.0.0.1", "localhost", "::1"} and not app.config["ACCESS_TOKEN"]:
        raise SystemExit("共享部署前请设置 STYLEMUSE_ACCESS_TOKEN")
    _job_service()
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
