"""
刘亮程风格写作 Skill - 文本加载与分块模块
"""

import re
from pathlib import Path
from typing import List, Optional

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from .config import (
    PROJECT_ROOT,
    CORPUS_FILE,
    CHUNKS_DIR,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    STYLE_GUIDE_FILE,
    FEW_SHOT_FILE,
)
from .epub_loader import load_epub_books


def load_corpus(corpus_path: Optional[Path] = None) -> str:
    """
    加载刘亮程原始语料

    Args:
        corpus_path: 语料文件路径，默认使用配置中的路径

    Returns:
        语料文本内容
    """
    path = corpus_path or CORPUS_FILE
    if not path.exists():
        raise FileNotFoundError(f"语料文件不存在: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    将长文本分割成小块

    Args:
        text: 原始文本
        chunk_size: 每个块的目标大小（字符数）
        chunk_overlap: 相邻块之间的重叠大小

    Returns:
        文本块列表
    """
    # 使用递归字符分割器，保持段落完整性
    splitter = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", "。", "！", "？", "——", "…"],
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )

    chunks = splitter.split_text(text)
    return chunks


def load_or_create_chunks(force_recreate: bool = False) -> List[Document]:
    """
    加载或创建文本块

    如果 chunks 目录中已有处理好的文件，直接加载；
    否则从语料文件创建并保存。

    Args:
        force_recreate: 是否强制重新创建

    Returns:
        Document 对象列表
    """
    chunks_file = CHUNKS_DIR / "chunks.json"

    # 如果已有缓存且不强制重建，直接加载
    if chunks_file.exists() and not force_recreate:
        import json
        with open(chunks_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [Document(page_content=item['content'], metadata=item['metadata']) for item in data]

    # 创建新的 chunks
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    text = load_corpus()
    chunks = split_into_chunks(text)

    # 转换为 Document 对象
    documents = []
    for i, chunk in enumerate(chunks):
        # 提取标题（如果 chunk 以标题开头）
        title_match = re.search(r'^【标题】(.+?)】', chunk)
        title = title_match.group(1) if title_match else f"片段 {i+1}"

        doc = Document(
            page_content=chunk,
            metadata={
                "chunk_id": i,
                "title": title,
                "source": "刘亮程文章汇总"
            }
        )
        documents.append(doc)

    # 保存到文件
    import json
    data = [{"content": doc.page_content, "metadata": doc.metadata} for doc in documents]
    with open(chunks_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return documents


def load_all_chunks(force_recreate: bool = False) -> List[Document]:
    """
    加载所有文本块（原始语料 + epub 书籍）

    Args:
        force_recreate: 是否强制重新创建

    Returns:
        Document 对象列表（原始语料 + epub）
    """
    corpus_docs = []
    if CORPUS_FILE.exists():
        corpus_docs = load_or_create_chunks(force_recreate=force_recreate)
    else:
        print(f"警告: 原始语料文件不存在 ({CORPUS_FILE})，仅使用 epub 内容")

    # 加载 epub chunks
    epub_dir = PROJECT_ROOT / "epub_data"
    if epub_dir.exists():
        epub_docs = load_epub_books(epub_dir, force_recreate=force_recreate)
    else:
        print(f"警告: epub_data 目录不存在，跳过 epub 加载")
        epub_docs = []

    # 合并
    all_docs = corpus_docs + epub_docs
    print(f"共加载 {len(all_docs)} 个文档片段（语料: {len(corpus_docs)} + epub: {len(epub_docs)}）")
    return all_docs


def load_style_guide() -> str:
    """加载风格指南"""
    if not STYLE_GUIDE_FILE.exists():
        return ""
    with open(STYLE_GUIDE_FILE, 'r', encoding='utf-8') as f:
        return f.read()


def load_few_shot_examples() -> str:
    """加载 Few-shot 示例"""
    if not FEW_SHOT_FILE.exists():
        return ""
    with open(FEW_SHOT_FILE, 'r', encoding='utf-8') as f:
        return f.read()
