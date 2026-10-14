"""
通用作家风格仿写 Skill - EPUB 解析模块
"""

import zipfile
from pathlib import Path
from typing import List
from bs4 import BeautifulSoup

from langchain_core.documents import Document
from .logger import get_logger
from .cache import CACHE_VERSION, file_manifest, read_cached_documents, write_cached_documents

log = get_logger(__name__)

def _validate_archive(path: Path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 128 * 1024 * 1024:
            raise ValueError("EPUB 解压内容超过限制（128 MB 或 2000 个条目）")


def _chapter_text(content) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for element in soup(["script", "style", "nav"]):
        element.decompose()
    body = soup.body or soup
    paragraphs = body.find_all(["p", "h1", "h2", "h3", "h4", "li"])
    return "\n\n".join(p.get_text("", strip=True) for p in paragraphs) if paragraphs else body.get_text(" ", strip=True)


def parse_epub(epub_path: Path) -> List[Document]:
    """解析单个 epub 文件"""
    from ebooklib import epub
    import ebooklib

    _validate_archive(epub_path)
    book = epub.read_epub(str(epub_path))

    # 获取书名
    titles = book.get_metadata("DC", "title")
    title = titles[0][0] if titles else epub_path.stem

    documents = []
    chapter_idx = 0

    items = [book.get_item_with_id(item_id) for item_id, _ in book.spine]
    if not items:
        items = list(book.get_items())
    for item in items:
        if item is not None and item.get_type() == ebooklib.ITEM_DOCUMENT and not isinstance(item, epub.EpubNav):
            content = item.get_content()
            text = _chapter_text(content)

            if len(text) < 50:
                continue

            chapter_idx += 1
            doc = Document(
                page_content=text,
                metadata={
                    "source": "epub",
                    "book_title": title,
                    "title": title,
                    "chapter": chapter_idx,
                    "epub_file": epub_path.name,
                },
            )
            documents.append(doc)

    return documents


def parse_epub_from_zip(epub_path: Path) -> List[Document]:
    """从 zip 直接解析 epub（备用方法）"""
    title = epub_path.stem
    _validate_archive(epub_path)
    documents = []
    chapter_idx = 0

    with zipfile.ZipFile(epub_path, "r") as z:
        for name in sorted(z.namelist()):
            if Path(name).suffix.lower() in {".xhtml", ".html", ".htm"}:
                with z.open(name) as f:
                    content = f.read()
                    text = _chapter_text(content)

                    if len(text) < 50:
                        continue

                    chapter_idx += 1
                    doc = Document(
                        page_content=text,
                        metadata={
                            "source": "epub",
                            "book_title": title,
                            "title": title,
                            "chapter": chapter_idx,
                            "epub_file": epub_path.name,
                        },
                    )
                    documents.append(doc)

    return documents


def load_epub_books(epub_dir: Path, cache_file: Path = None, force_recreate: bool = False) -> List[Document]:
    """
    加载目录下所有 epub 文件。

    Args:
        epub_dir: epub 文件目录
        cache_file: 缓存文件路径（可选）
        force_recreate: 是否强制重新解析

    Returns:
        Document 列表
    """
    epub_dir = Path(epub_dir)
    manifest = {"version": CACHE_VERSION, "files": file_manifest(epub_dir, ".epub")}

    if cache_file and cache_file.exists() and not force_recreate:
        cached = read_cached_documents(cache_file, manifest)
        if cached is not None:
            return cached

    epub_files = sorted(epub_dir.glob("*.epub"))
    if not epub_files:
        log.warning(f"提示: 在 {epub_dir} 中未找到 epub 文件")
        return []

    all_docs = []
    for epub_path in epub_files:
        try:
            docs = parse_epub(epub_path)
            if not docs:
                docs = parse_epub_from_zip(epub_path)
            log.info(f"  解析 {epub_path.name}: {len(docs)} 个片段")
            all_docs.extend(docs)
        except Exception as e:
            log.error(f"  解析 {epub_path.name} 失败: {e}，尝试备用方法...")
            try:
                docs = parse_epub_from_zip(epub_path)
                log.info(f"  备用方法解析 {epub_path.name}: {len(docs)} 个片段")
                all_docs.extend(docs)
            except Exception as e2:
                raise ValueError(f"无法解析 EPUB: {epub_path.name}") from e2
        if not docs:
            raise ValueError(f"EPUB 中未找到有效正文: {epub_path.name}")

    # 保存缓存
    if cache_file:
        write_cached_documents(cache_file, manifest, all_docs)
        log.info(f"  epub 缓存已保存: {cache_file}")

    return all_docs
