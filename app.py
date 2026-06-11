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
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from flask import Flask, request, jsonify, send_from_directory, send_file, Response
from skills.author_style import (
    AuthorStyleSkill,
    create_author,
    list_authors,
    delete_author,
    get_author_info,
)
from skills.author_style.safety import resolve_under_base, safe_filename, unique_path

app = Flask(__name__, static_folder="static")

ENV_FILE = project_root / ".env"
MODELS_FILE = project_root / "models.json"
SUPPORTED_UPLOAD_SUFFIXES = {".txt", ".epub"}
ALLOWED_MODEL_PROVIDERS = {"openai", "anthropic"}
MAX_CUSTOM_MODELS = 50
MAX_MODEL_FIELD_LENGTH = 300


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
                filename.write_bytes(f.read())

            create_author(name=name, source_path=None, analyze=True, build_index=True)
        else:
            data = request.get_json()
            if not data:
                return jsonify({"ok": False, "error": "缺少请求数据"}), 400
            name = data.get("name", "").strip()
            if not name:
                return jsonify({"ok": False, "error": "缺少作家名称"}), 400
            if data.get("source_path"):
                return jsonify({"ok": False, "error": "Web API 不接受 source_path，请使用文件上传"}), 400
            create_author(name=name, source_path=None)

        info = get_author_info(name)
        return jsonify({"ok": True, "data": info})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/author/<name>", methods=["DELETE"])
def api_delete_author(name):
    try:
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
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "缺少请求数据"}), 400

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
            request_options=_request_generation_options(data),
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
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "缺少请求数据"}), 400

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
                for chunk in skill.write_stream(
                    topic=topic,
                    tone=tone,
                    length=length,
                    include_retrieval=include_retrieval,
                ):
                    article_chunks.append(chunk)
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"

                # 保存文章
                article = "".join(article_chunks)
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
                    request_options=_request_generation_options(data),
                )
                version = _load_json_file(version_path)

                yield f"data: {json.dumps({'type': 'done', 'article': article, 'topic': topic, 'author': author, 'saved_path': saved_path, 'review_path': str(review_path) if review_path else '', 'version_path': str(version_path), 'version': version, 'plagiarism': skill.last_plagiarism_result, 'review': skill.last_review_result}, ensure_ascii=False)}\n\n"
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

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/rewrite", methods=["POST"])
def api_rewrite():
    """Rewrite an article using the latest review feedback."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "缺少请求数据"}), 400

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
            request_options=_request_generation_options(data),
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
            "EMBEDDING_API_KEY": _mask_key(env_vars.get("EMBEDDING_API_KEY", "")),
            "EMBEDDING_API_KEY_SET": bool(env_vars.get("EMBEDDING_API_KEY", "")),
        },
    })


@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存 .env 配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "缺少请求数据"}), 400

        env_vars = _read_env_file()

        # 更新字段（空字符串表示不修改，"__CLEAR__" 表示清除）
        for key in ["LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL"]:
            if key in data and data[key] != "":
                env_vars[key] = data[key]

        # API Key 特殊处理：不传或为空则保留原值
        for key in ["LLM_API_KEY", "EMBEDDING_API_KEY"]:
            if key in data and data[key] and data[key] != "__KEEP__":
                env_vars[key] = data[key]

        _write_env_file(env_vars)

        # 重新加载环境变量
        from dotenv import load_dotenv
        load_dotenv(override=True)

        return jsonify({"ok": True, "message": "配置已保存，需要重启服务生效"})
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

def _read_env_file() -> dict:
    """读取 .env 文件为字典"""
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _write_env_file(env: dict):
    """将字典写入 .env 文件"""
    lines = []
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
                    lines.append(f"{key}={env.pop(key)}")
                else:
                    lines.append(line)
            else:
                lines.append(line)

    # 追加新增的键
    for key, value in env.items():
        lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mask_key(key: str) -> str:
    """密钥脱敏"""
    if not key or len(key) < 8:
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
        {"provider": "openai", "name": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "label": "DeepSeek"},
        {"provider": "openai", "name": "qwen-turbo", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "label": "通义千问"},
        {"provider": "openai", "name": "glm-4-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "label": "智谱 GLM"},
        {"provider": "anthropic", "name": "MiniMax-M2.7", "base_url": "https://api.minimaxi.com/anthropic", "label": "MiniMax"},
        {"provider": "openai", "name": "gpt-4o", "base_url": "", "label": "OpenAI"},
    ]


def _save_models(models: list):
    """保存用户自定义模型列表"""
    MODELS_FILE.write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")


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

        cleaned.append({
            "provider": provider,
            "name": name,
            "base_url": base_url,
            "label": label,
        })
    return cleaned


def _resolve_save_dir(save_dir: str, author: str) -> Path:
    """Resolve the requested save directory within the project workspace."""
    if save_dir and save_dir.strip():
        candidate = Path(save_dir.strip())
        if not candidate.is_absolute():
            candidate = project_root / candidate
        target = resolve_under_base(project_root, candidate, "保存位置")
    else:
        target = project_root / "authors" / author / "output"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _save_article(author: str, topic: str, article: str, save_dir: str = "") -> Path:
    """Save a generated article using a sanitized topic filename."""
    target_dir = _resolve_save_dir(save_dir, author)
    filename = unique_path(target_dir, safe_filename(topic, default="article", suffix=".txt"))
    filename.write_text(article, encoding="utf-8")
    return filename


def _save_review(article_path: Path, review: dict) -> Path | None:
    """Save the structured review report next to a generated article."""
    if not review:
        return None
    path = Path(article_path)
    review_path = path.with_suffix(".review.json")
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    return review_path


def _save_version_metadata(
    article_path: Path,
    *,
    kind: str,
    author: str,
    topic: str,
    article: str,
    review: dict | None,
    plagiarism: dict | None,
    parent_version: str = "",
    request_options: dict | None = None,
) -> Path:
    """Save version-chain metadata next to a generated article."""
    path = Path(article_path)
    review_path = path.with_suffix(".review.json") if review else None
    version_path = path.with_suffix(".version.json")
    metadata = {
        "version_id": path.stem,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "author": author,
        "topic": topic,
        "article_path": str(path),
        "review_path": str(review_path) if review_path else "",
        "parent_version": str(parent_version or ""),
        "article_chars": len(article or ""),
        "request": request_options or {},
        "review_summary": _summarize_review(review),
        "plagiarism_summary": _summarize_plagiarism(plagiarism),
    }
    version_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return version_path


def _request_generation_options(data: dict) -> dict:
    """Return non-secret generation options suitable for version metadata."""
    return {
        "tone": data.get("tone", "default"),
        "length": data.get("length", "medium"),
        "include_retrieval": data.get("include_retrieval", True),
        "model": data.get("model", ""),
        "temperature": data.get("temperature", None),
        "max_tokens": data.get("max_tokens", None),
    }


def _summarize_review(review: dict | None) -> dict:
    if not review:
        return {}
    requirement = review.get("requirement") or {}
    style = review.get("style") or {}
    plagiarism = review.get("plagiarism") or {}
    return {
        "decision": review.get("decision"),
        "passed": review.get("passed"),
        "score": review.get("score"),
        "requirement_score": requirement.get("score"),
        "style_score": style.get("score"),
        "plagiarism_score": plagiarism.get("score"),
        "plagiarism_risk": plagiarism.get("risk"),
        "suggestion_count": len(review.get("suggestions") or []),
    }


def _summarize_plagiarism(plagiarism: dict | None) -> dict:
    if not plagiarism:
        return {}
    return {
        "passed": plagiarism.get("passed"),
        "max_common": plagiarism.get("max_common"),
        "similar_doc_count": len(plagiarism.get("similar_docs") or []),
        "warning": plagiarism.get("warning", ""),
    }


def _load_json_file(path: Path | None) -> dict:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    print("=" * 50)
    print("通用作家风格仿写 Skill")
    print("访问地址: http://localhost:5000")
    print("=" * 50)
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
