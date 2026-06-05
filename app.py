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
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from flask import Flask, request, jsonify, send_from_directory, send_file
from skills.author_style import (
    AuthorStyleSkill,
    create_author,
    list_authors,
    delete_author,
    get_author_info,
)

app = Flask(__name__, static_folder="static")

ENV_FILE = project_root / ".env"
MODELS_FILE = project_root / "models.json"


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

            import tempfile, shutil
            temp_dir = Path(tempfile.mkdtemp(prefix="author_upload_"))
            files = request.files.getlist("files")
            if not files:
                return jsonify({"ok": False, "error": "请上传至少一个文件"}), 400

            for f in files:
                if f.filename:
                    (temp_dir / f.filename).write_bytes(f.read())

            try:
                create_author(name=name, source_path=str(temp_dir))
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            data = request.get_json()
            if not data:
                return jsonify({"ok": False, "error": "缺少请求数据"}), 400
            name = data.get("name", "").strip()
            if not name:
                return jsonify({"ok": False, "error": "缺少作家名称"}), 400
            create_author(name=name, source_path=data.get("source_path") or None)

        info = get_author_info(name)
        return jsonify({"ok": True, "data": info})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/author/<name>", methods=["DELETE"])
def api_delete_author(name):
    try:
        success = delete_author(name, confirm=False)
        return jsonify({"ok": success})
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
        save_dir = data.get("save_dir", "").strip()
        saved_path = ""
        if save_dir:
            save_path = Path(save_dir)
            save_path.mkdir(parents=True, exist_ok=True)
            filename = save_path / f"{topic}.txt"
            filename.write_text(article, encoding="utf-8")
            saved_path = str(filename)
        else:
            # 默认保存到作家 output 目录
            output_dir = project_root / "authors" / author / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = output_dir / f"{topic}.txt"
            filename.write_text(article, encoding="utf-8")
            saved_path = str(filename)

        return jsonify({
            "ok": True,
            "data": {
                "article": article,
                "topic": topic,
                "author": author,
                "saved_path": saved_path,
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
        filename = data.get("filename", "article.txt")

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
        models = data.get("models", [])
        _save_models(models)
        return jsonify({"ok": True})
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


if __name__ == "__main__":
    print("=" * 50)
    print("通用作家风格仿写 Skill")
    print("访问地址: http://localhost:5000")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True)
