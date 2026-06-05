"""
通用作家风格仿写 Skill - 文本加载与分块模块
"""

import re
import json
from pathlib import Path
from typing import List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from .epub_loader import load_epub_books
from .logger import get_logger

log = get_logger(__name__)


def load_txt_files(works_dir: Path) -> List[Document]:
    """
    加载目录下所有 .txt 文件。

    Args:
        works_dir: txt 文件目录

    Returns:
        Document 列表
    """
    works_dir = Path(works_dir)
    if not works_dir.exists():
        return []

    documents = []
    for txt_file in sorted(works_dir.glob("*.txt")):
        try:
            with open(txt_file, "r", encoding="utf-8") as f:
                text = f.read()
            if len(text.strip()) < 50:
                continue
            doc = Document(
                page_content=text,
                metadata={
                    "source": "txt",
                    "filename": txt_file.name,
                    "title": txt_file.stem,
                },
            )
            documents.append(doc)
        except Exception as e:
            log.error(f"  读取 {txt_file.name} 失败: {e}")

    return documents


def split_documents(documents: List[Document], chunk_size: int = 100, chunk_overlap: int = 20) -> List[Document]:
    """
    将文档列表切分为小块。

    Args:
        documents: 原始 Document 列表
        chunk_size: 每块字符数
        chunk_overlap: 重叠字符数

    Returns:
        切分后的 Document 列表
    """
    splitter = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", "。", "！", "？", "——", "…", "，", "、", "；", "："],
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )

    split_docs = []
    for doc in documents:
        chunks = splitter.split_text(doc.page_content)
        for i, chunk in enumerate(chunks):
            # 尝试提取标题
            title_match = re.search(r"^【(.+?)】", chunk)
            title = title_match.group(1) if title_match else doc.metadata.get("title", f"片段 {i+1}")

            split_doc = Document(
                page_content=chunk,
                metadata={**doc.metadata, "chunk_id": i, "title": title},
            )
            split_docs.append(split_doc)

    return split_docs


def load_all_chunks(author_dir: Path, chunk_size: int = 100, chunk_overlap: int = 20, force_recreate: bool = False) -> List[Document]:
    """
    加载作家的所有文本块（txt + epub），带缓存。

    Args:
        author_dir: 作家工作空间目录
        chunk_size: 每块字符数
        chunk_overlap: 重叠字符数
        force_recreate: 是否强制重建

    Returns:
        Document 列表
    """
    cache_file = author_dir / "cache" / "chunks.json"

    if cache_file.exists() and not force_recreate:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            log.info(f"从缓存加载 {len(data)} 个文本块")
            return [Document(page_content=item["content"], metadata=item["metadata"]) for item in data]

    all_docs = []

    # 加载 txt 文件
    works_dir = author_dir / "works"
    if works_dir.exists():
        txt_docs = load_txt_files(works_dir)
        log.info(f"  txt 文件: {len(txt_docs)} 个文档")
        all_docs.extend(txt_docs)

    # 加载 epub 文件
    epub_dir = author_dir / "epub"
    if epub_dir.exists():
        epub_cache = author_dir / "cache" / "epub_raw.json"
        epub_docs = load_epub_books(epub_dir, cache_file=epub_cache, force_recreate=force_recreate)
        log.info(f"  epub 文件: {len(epub_docs)} 个文档")
        all_docs.extend(epub_docs)

    if not all_docs:
        log.warning(f"警告: 未找到任何文本文件 ({author_dir})")
        return []

    # 切分
    split_docs = split_documents(all_docs, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    log.info(f"  切分完成: {len(split_docs)} 个文本块")

    # 保存缓存
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = [{"content": doc.page_content, "metadata": doc.metadata} for doc in split_docs]
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return split_docs


def load_file_content(file_path: Path) -> str:
    """读取单个文件内容"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()
