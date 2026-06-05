"""
刘亮程风格写作 Skill

一个可复用的 LangChain RAG Chain，用于模仿刘亮程的写作风格进行散文创作。
"""

from .rag_chain import LiuLiangchengSkill, write, retrieve_relevant_context, extract_text_from_response
from .loader import load_or_create_chunks, load_corpus, load_style_guide, load_few_shot_examples
from .style_prompt import build_system_prompt, build_user_prompt, TONE_OPTIONS, LENGTH_OPTIONS
from .config import (
    DEFAULT_MODEL,
    MAX_TOKENS,
    TEMPERATURE,
    RETRIEVAL_TOP_K,
)

__all__ = [
    # 核心 Skill
    "LiuLiangchengSkill",
    "write",
    "retrieve_relevant_context",
    # 工具函数
    "load_or_create_chunks",
    "load_corpus",
    "load_style_guide",
    "load_few_shot_examples",
    "build_system_prompt",
    "build_user_prompt",
    # 配置
    "TONE_OPTIONS",
    "LENGTH_OPTIONS",
    "DEFAULT_MODEL",
    "MAX_TOKENS",
    "TEMPERATURE",
    "RETRIEVAL_TOP_K",
]

__version__ = "0.1.0"
