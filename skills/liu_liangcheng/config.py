"""
刘亮程风格写作 Skill - 配置模块
==================================

【参数修改指南】

本文件包含所有可配置参数，修改后需重启 Python 进程生效。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【第一部分：API 密钥配置】

API 密钥获取地址：
- MiniMax API: https://platform.minimaxi.com/
- 千问 API (KWEN_API): https://dashscope.console.aliyun.com/

优先级：环境变量 > Windows 注册表 > 本文件默认值

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# 【路径配置】（一般不需要修改）
# ============================================================================

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

# ============================================================================
# 【API 密钥配置】
# ============================================================================

# MiniMax API 密钥（用于大语言模型生成）
# 环境变量: MINIMAX_API 或 MINIMAX_API_KEY
# 注册表: HKCU\Environment\MINIMAX_API
# --- 请在 .env 文件中设置: MINIMAX_API=your_key_here ---
MINIMAX_API = os.environ.get("MINIMAX_API") or os.environ.get("MINIMAX_API_KEY")

# MiniMax API 地址（一般不需要修改）
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic")

# MiniMax Group ID（部分 API 需要）
# --- 请在 .env 文件中设置: MINIMAX_GROUP_ID=your_group_id ---
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID")

# 千问 API 密钥（用于 Embedding 向量化）
# 环境变量: KWEN_API
# 注册表: HKCU\Environment\KWEN_API
# --- 请在 .env 文件中设置: KWEN_API=your_key_here ---
KWEN_API = os.environ.get("KWEN_API")


# ============================================================================
# 【第二部分：Embedding 模型配置】
# ============================================================================

# Embedding 模型名称
# 可选模型:
#   - text-embedding-v3 (推荐，1024维)
#   - text-embedding-v2 (768维，已deprecated)
EMBEDDING_MODEL = "text-embedding-v3"

# 向量维度（由模型决定，一般不需修改）
EMBEDDING_DIM = 1024


# ============================================================================
# 【第三部分：向量存储配置】
# ============================================================================

# 向量数据库类型
# 可选: "faiss" / "chroma" / "in-memory"
# 推荐: "faiss"（支持本地持久化，跨进程共享）
VECTOR_STORE_TYPE = "faiss"

# 向量库持久化目录
#   - FAISS: 会生成 index.faiss + index.pkl
#   - Chroma: 会生成 chroma.sqlite3
VECTOR_STORE_PERSIST_DIR = PROJECT_ROOT / "data" / "embeddings"


# ============================================================================
# 【第四部分：文本分块配置】
# ============================================================================

# 每个文本块的字符数（建议 100-200）
# 越小越碎片化，检索越精准但上下文越少
# 越大上下文越丰富但容易复制原文
CHUNK_SIZE = 100

# 相邻块之间的重叠字符数
# 建议为 CHUNK_SIZE 的 20%左右
CHUNK_OVERLAP = 20


# ============================================================================
# 【第五部分：检索配置】
# ============================================================================

# 检索返回的相关段落数量（建议 2-3）
# 越多上下文越丰富，但复制风险增加
RETRIEVAL_TOP_K = 3

# 相似度过滤阈值
# score >= 0.15 表示相似度 <= 85%
# 即: 相似度 > 85% 的片段会被过滤（防止复制原文）
SIMILARITY_THRESHOLD = 0.15

# 检索时多取结果的倍数（用于过滤+打乱后仍有足够结果）
RETRIEVAL_MULTIPLIER = 3


# ============================================================================
# 【第六部分：生成模型配置】
# ============================================================================

# 使用的模型名称
# 可选:
#   - MiniMax-M2.7
#   - MiniMax-Text-01
#   - 其他 MiniMax 支持的模型
DEFAULT_MODEL = "MiniMax-M2.7"

# 最大生成长度（token 数）
# 约等于中文字符数 * 1.5
# 建议:
#   - short: 1000
#   - medium: 2500
#   - long: 4000
MAX_TOKENS = 2500

# 生成温度（随机性）
# 建议:
#   - 0.8-1.0: 更有创意，更少复制原文
#   - 0.5-0.7: 更稳定但可能复制
#   - 0.0-0.3: 几乎确定输出，不推荐
TEMPERATURE = 1.0


# ============================================================================
# 【第七部分：文件路径配置】
# ============================================================================

# 原始语料文件
CORPUS_FILE = PROJECT_ROOT / "works" / "刘亮程文章汇总.txt"

# 切分后的 chunk 缓存目录
CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"

# Prompt 模板目录
PROMPTS_DIR = PROJECT_ROOT / "prompts"
STYLE_GUIDE_FILE = PROMPTS_DIR / "style_guide.md"
FEW_SHOT_FILE = PROMPTS_DIR / "few_shot_examples.md"


# ============================================================================
# 【函数区】一般不需要修改
# ============================================================================

def _read_registry(name: str) -> Optional[str]:
    """从 Windows 注册表读取环境变量（仅 Windows 可用）"""
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


def get_api_key() -> Optional[str]:
    """获取 MiniMax API Key，优先从环境变量，失败则尝试注册表"""
    if MINIMAX_API:
        return MINIMAX_API
    return _read_registry("MINIMAX_API")


def get_group_id() -> Optional[str]:
    """获取 MiniMax Group ID"""
    if MINIMAX_GROUP_ID:
        return MINIMAX_GROUP_ID
    return _read_registry("MINIMAX_GROUP_ID")


def get_kwen_api_key() -> Optional[str]:
    """获取千问 API Key"""
    if KWEN_API:
        return KWEN_API
    return _read_registry("KWEN_API")
