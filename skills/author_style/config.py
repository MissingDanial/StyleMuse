"""
通用作家风格仿写 Skill - 配置模块

支持全局默认配置 + 每位作家独立配置覆盖。
模型可自由配置，兼容 OpenAI API 格式的所有大模型。
"""

import os
import json
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# 路径配置
# ============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent
AUTHORS_DIR = PROJECT_ROOT / "authors"

# ============================================================================
# 环境变量读取
# ============================================================================

def _read_registry(name: str) -> Optional[str]:
    """从 Windows 注册表读取环境变量"""
    try:
        import subprocess
        result = subprocess.check_output(
            f'reg query "HKCU\\Environment" /v {name}',
            shell=True,
            stderr=subprocess.DEVNULL
        )
        lines = result.decode('gbk', errors='replace').split('\n')
        for line in lines:
            if name in line:
                parts = line.strip().split()
                if len(parts) >= 3:
                    return parts[-1]
    except Exception:
        pass
    return None


def _env(name: str, default: str = None) -> Optional[str]:
    """读取环境变量，支持注册表回退"""
    val = os.environ.get(name)
    if val:
        return val
    val = _read_registry(name)
    return val or default


# ============================================================================
# LLM 配置（大语言模型）
# ============================================================================

# provider: "openai" (兼容所有 OpenAI API 格式) 或 "anthropic"
LLM_PROVIDER = _env("LLM_PROVIDER", "openai")
LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = _env("LLM_BASE_URL")
LLM_API_KEY = _env("LLM_API_KEY")

# 向后兼容 MiniMax 旧配置
if not LLM_API_KEY:
    LLM_API_KEY = _env("MINIMAX_API") or _env("MINIMAX_API_KEY")
if not LLM_BASE_URL and LLM_API_KEY and _env("MINIMAX_API"):
    LLM_BASE_URL = "https://api.minimaxi.com/anthropic"
    LLM_PROVIDER = "anthropic"
    LLM_MODEL = _env("LLM_MODEL", "MiniMax-M2.7")

# Embedding 配置
EMBEDDING_PROVIDER = _env("EMBEDDING_PROVIDER", "dashscope")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "text-embedding-v3")
EMBEDDING_API_KEY = _env("EMBEDDING_API_KEY") or _env("KWEN_API")

# 向后兼容千问旧配置
if not EMBEDDING_API_KEY:
    EMBEDDING_API_KEY = _env("KWEN_API")


def get_api_key() -> Optional[str]:
    """获取 LLM API Key"""
    return LLM_API_KEY


def get_embedding_api_key() -> Optional[str]:
    """获取 Embedding API Key"""
    return EMBEDDING_API_KEY


def get_group_id() -> Optional[str]:
    """获取 MiniMax Group ID（向后兼容）"""
    return _env("MINIMAX_GROUP_ID")


# ============================================================================
# 全局默认参数（可被作家 config.json 覆盖）
# ============================================================================

DEFAULTS = {
    # LLM
    "llm_provider": LLM_PROVIDER or "openai",
    "llm_model": LLM_MODEL or "deepseek-chat",
    "llm_base_url": LLM_BASE_URL or "",
    "llm_api_key": LLM_API_KEY or "",

    # Embedding
    "embedding_provider": EMBEDDING_PROVIDER or "dashscope",
    "embedding_model": EMBEDDING_MODEL or "text-embedding-v3",
    "embedding_api_key": EMBEDDING_API_KEY or "",

    # 分块
    "chunk_size": 100,
    "chunk_overlap": 20,

    # 检索
    "retrieval_top_k": 3,
    "similarity_threshold": 0.15,
    "retrieval_multiplier": 3,

    # 生成
    "max_tokens": 2500,
    "temperature": 1.0,

    # tone / length 选项
    "tone_options": {
        "default": "模仿该作家的核心风格，保持自然流畅",
        "humorous": "更强调幽默感",
        "philosophical": "更偏重哲学思考",
        "poetic": "更诗意化",
        "simple": "更朴实自然",
    },
    "length_options": {
        "short": "400-500字",
        "medium": "800-1000字",
        "long": "1500-2000字",
    },
}


def get_author_dir(name: str) -> Path:
    """获取作家工作空间目录"""
    return AUTHORS_DIR / name


def load_author_config(name: str) -> dict:
    """
    加载作家配置，与全局默认值合并。

    Args:
        name: 作家名称（目录名）

    Returns:
        合并后的配置字典
    """
    config = dict(DEFAULTS)
    config_file = get_author_dir(name) / "config.json"

    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            author_config = json.load(f)
            for key, value in author_config.items():
                if key in config and isinstance(config[key], dict) and isinstance(value, dict):
                    config[key] = {**config[key], **value}
                else:
                    config[key] = value

    return config


def save_author_config(name: str, config: dict):
    """保存作家配置到 config.json"""
    config_file = get_author_dir(name) / "config.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_llm(config: dict = None):
    """
    根据配置创建 LLM 实例。

    支持的 provider:
      - "openai": 兼容所有 OpenAI API 格式（DeepSeek、通义千问、MiniMax OpenAI模式等）
      - "anthropic": Anthropic 兼容接口

    Args:
        config: 配置字典，包含 llm_provider, llm_model, llm_base_url, llm_api_key

    Returns:
        LangChain ChatModel 实例
    """
    cfg = config or DEFAULTS
    provider = cfg.get("llm_provider", "openai")
    model = cfg.get("llm_model", "deepseek-chat")
    base_url = cfg.get("llm_base_url", "")
    api_key = cfg.get("llm_api_key", "")

    if not api_key:
        raise ValueError(
            "未配置 LLM API Key。请在 .env 文件中设置 LLM_API_KEY，"
            "或在作家 config.json 中设置 llm_api_key"
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs = {
            "model": model,
            "api_key": api_key,
            "max_tokens": cfg.get("max_tokens", 2500),
            "temperature": cfg.get("temperature", 1.0),
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatAnthropic(**kwargs)

    else:  # openai 兼容
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": model,
            "api_key": api_key,
            "max_tokens": cfg.get("max_tokens", 2500),
            "temperature": cfg.get("temperature", 1.0),
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)


def get_embeddings(config: dict = None):
    """
    根据配置创建 Embedding 实例。

    Args:
        config: 配置字典

    Returns:
        LangChain Embeddings 实例
    """
    cfg = config or DEFAULTS
    provider = cfg.get("embedding_provider", "dashscope")
    model = cfg.get("embedding_model", "text-embedding-v3")
    api_key = cfg.get("embedding_api_key", "")

    if not api_key:
        raise ValueError(
            "未配置 Embedding API Key。请在 .env 文件中设置 EMBEDDING_API_KEY 或 KWEN_API"
        )

    if provider == "dashscope":
        from .rag_chain import QwenEmbeddings
        return QwenEmbeddings(api_key=api_key, model=model)

    elif provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(
            model=model,
            api_key=api_key,
            base_url=cfg.get("embedding_base_url"),
        )

    else:
        raise ValueError(f"不支持的 Embedding provider: {provider}")
