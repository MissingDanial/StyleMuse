"""
通用作家风格仿写 Skill - 文本加载与分块模块
"""

import hashlib
import re
from pathlib import Path
from typing import List

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from .epub_loader import load_epub_books
from .logger import get_logger
from .cache import CACHE_VERSION, file_manifest, read_cached_documents, write_cached_documents

log = get_logger(__name__)

SOURCE_PARSER_VERSION = 1
_ARTICLE_TITLE = re.compile(r"(?m)^[ \t]*【标题】[ \t]*([^\r\n]+)")
_BODY_MARKER = re.compile(r"(?m)^[ \t]*【正文】[ \t]*")


def _source_document(text: str, filename: str, start: int, end: int, **metadata) -> Document:
    content = text[start:end]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return Document(page_content=content, metadata={
        "source": "txt", "filename": filename,
        "source_id": hashlib.sha256(f"{filename}:{start}:{digest}".encode("utf-8")).hexdigest(),
        "content_sha256": digest, "source_start": start, "source_end": end,
        "source_line": text.count("\n", 0, start) + 1,
        "offset_basis": "unicode_characters_lf", **metadata,
    })


def _parse_txt(text: str, filename: str) -> List[Document]:
    """Split article bundles without indexing their labels or trailing separators."""
    titles = list(_ARTICLE_TITLE.finditer(text))
    if not titles:
        return [_source_document(text, filename, 0, len(text), title=Path(filename).stem,
                                 source_type="work", style_eligible=True, attribution="workspace")]
    documents = []
    for index, title in enumerate(titles):
        end = titles[index + 1].start() if index + 1 < len(titles) else len(text)
        section = text[title.end():end]
        body = _BODY_MARKER.search(section)
        if not body:
            raise ValueError(f"{filename} 的《{title.group(1).strip()}》缺少【正文】")
        header = section[:body.start()]
        fields = dict(re.findall(r"(?m)^[ \t]*【(来源|分类|作者)】[ \t]*([^\r\n]*)", header))
        start = title.end() + body.end()
        # Only trim at the edges so offsets still refer to the original LF text.
        raw = text[start:end]
        trailing = re.search(r"(?:\s*\n[ \t]*={3,}[ \t]*)+\s*$", raw)
        if trailing:
            end = start + trailing.start()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        category = fields.get("分类", "").strip()
        eligible = category == "当代名篇" or not category
        documents.append(_source_document(
            text, filename, start, end, title=title.group(1).strip(),
            source_url=fields.get("来源", "").strip(), category=category,
            declared_author=fields.get("作者", "").strip(),
            attribution="declared" if fields.get("作者") else "unverified",
            source_type="work" if eligible else "reference", style_eligible=eligible,
        ))
    return documents


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
            documents.extend(_parse_txt(text, txt_file.name))
        except (OSError, UnicodeError) as e:
            raise ValueError(f"读取 {txt_file.name} 失败，请使用 UTF-8 编码") from e

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
        separators=["\n\n", "\n", "。", "！", "？", "——", "…", "，", "、", "；", "：", ""],
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        add_start_index=True,
    )

    split_docs = []
    for doc in documents:
        chunks = splitter.create_documents([doc.page_content], metadatas=[doc.metadata])
        for i, chunk in enumerate(chunks):
            offset = chunk.metadata.pop("start_index")
            chunk.metadata.update({
                "chunk_id": len(split_docs), "article_chunk_id": i,
                "source_start": doc.metadata.get("source_start", 0) + offset,
                "source_end": doc.metadata.get("source_start", 0) + offset + len(chunk.page_content),
                "paragraph_id": len(re.findall(r"\n\s*\n", doc.page_content[:offset])) + 1,
                "source_line": doc.metadata.get("source_line", 1) + doc.page_content.count("\n", 0, offset),
            })
            split_docs.append(chunk)

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
    author_dir = Path(author_dir)
    cache_file = author_dir / "cache" / "chunks.json"
    manifest = {
        "version": CACHE_VERSION,
        "source_parser_version": SOURCE_PARSER_VERSION,
        "txt": file_manifest(author_dir / "works", ".txt"),
        "epub": file_manifest(author_dir / "epub", ".epub"),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
    }

    if cache_file.exists() and not force_recreate:
        cached = read_cached_documents(cache_file, manifest)
        if cached is not None:
            log.info(f"从缓存加载 {len(cached)} 个文本块")
            return cached

    all_docs = [doc for doc in load_source_documents(author_dir, force_recreate=force_recreate)
                if doc.metadata.get("style_eligible", True)]

    if not all_docs:
        log.warning(f"警告: 未找到任何文本文件 ({author_dir})")
        write_cached_documents(cache_file, manifest, [])
        return []

    # 切分
    split_docs = split_documents(all_docs, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    log.info(f"  切分完成: {len(split_docs)} 个文本块")

    # 保存缓存
    write_cached_documents(cache_file, manifest, split_docs)

    return split_docs


def load_source_documents(author_dir: Path, force_recreate: bool = False) -> List[Document]:
    """Unsplit source texts for style statistics and cross-chunk copy detection."""
    author_dir = Path(author_dir)
    documents = load_txt_files(author_dir / "works")
    if (author_dir / "epub").exists():
        documents.extend(load_epub_books(author_dir / "epub", author_dir / "cache" / "epub_raw.json",
                                         force_recreate=force_recreate))
    for index, doc in enumerate(documents):
        if "source_id" not in doc.metadata:
            digest = hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()
            doc.metadata.update({"source_id": hashlib.sha256(
                f"{doc.metadata}:{index}:{digest}".encode("utf-8")).hexdigest(),
                "content_sha256": digest, "source_type": "work", "style_eligible": True,
                "source_start": 0, "source_end": len(doc.page_content),
                "offset_basis": "extracted_chapter_unicode_characters"})
    return documents


def load_file_content(file_path: Path) -> str:
    """读取单个文件内容"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def reference_documents(style_guide: str = "", few_shot: str = "", retrieved_context: str = "") -> List[Document]:
    """Actual prompt references, including the same few-shot limit as generation."""
    references = []
    for kind, filename, title, text in (
        ("style_guide", "style_guide.md", "风格指南", style_guide),
        ("few_shot", "few_shot.md", "固定风格示例", few_shot[:2000]),
        ("retrieval", "", "本次检索参考", retrieved_context),
    ):
        if text.strip():
            references.append(_source_document(text, filename or kind, 0, len(text),
                                              title=title, source_type=kind, style_eligible=False))
    return references
