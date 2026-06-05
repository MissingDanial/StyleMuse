"""
通用作家风格仿写 Skill

支持上传任意作家的 epub/txt 作品，自动分析风格、构建 RAG、生成仿写。
"""

from .rag_chain import AuthorStyleSkill, extract_text_from_response
from .author_manager import create_author, list_authors, delete_author, get_author_info
from .analyzer import analyze_author
from .loader import load_all_chunks
from .style_prompt import build_system_prompt, build_user_prompt

__all__ = [
    # 核心 Skill
    "AuthorStyleSkill",
    # 作家管理
    "create_author",
    "list_authors",
    "delete_author",
    "get_author_info",
    # 分析器
    "analyze_author",
    # 工具函数
    "extract_text_from_response",
    "load_all_chunks",
    "build_system_prompt",
    "build_user_prompt",
]

__version__ = "0.2.0"
