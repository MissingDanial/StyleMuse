"""
刘亮程风格写作 Skill - epub 解析模块
"""

import re
import json
import zipfile
import warnings
from pathlib import Path
from typing import List
from bs4 import BeautifulSoup

from langchain_core.documents import Document

from .config import CHUNKS_DIR

# Suppress BeautifulSoup XML parser warning
warnings.filterwarnings('ignore')


def parse_epub(epub_path: Path) -> List[Document]:
    """
    解析单个 epub 文件

    Args:
        epub_path: epub 文件路径

    Returns:
        Document 对象列表
    """
    from ebooklib import epub

    book = epub.read_epub(str(epub_path))

    # 获取书名
    title = None
    for meta in book.metadata.get('DC', {}).get('title', []):
        if meta.lang == 'zh-cn' or not title:
            title = str(meta.content) if hasattr(meta, 'content') else str(meta)
            break
    if not title:
        title = epub_path.stem

    documents = []
    chapter_idx = 0

    for item in book.get_items():
        if item.get_type() == 9:  # XHTML/Document type
            content = item.get_content()

            # 使用 BeautifulSoup XML 解析器提取文本
            soup = BeautifulSoup(content, 'xml')
            text = soup.get_text(separator=' ', strip=True)

            if len(text) < 50:  # 跳过太短的片段
                continue

            chapter_idx += 1
            doc = Document(
                page_content=text,
                metadata={
                    "source": "epub",
                    "book_title": title,
                    "chapter": chapter_idx,
                    "epub_file": epub_path.name
                }
            )
            documents.append(doc)

    return documents


def parse_epub_from_zip(epub_path: Path) -> List[Document]:
    """
    从 zip 直接解析 epub（备用方法，不依赖 ebooklib）

    Args:
        epub_path: epub 文件路径

    Returns:
        Document 对象列表
    """
    # 获取书名（从文件名）
    title = epub_path.stem

    documents = []
    chapter_idx = 0

    with zipfile.ZipFile(epub_path, 'r') as z:
        for name in z.namelist():
            if name.startswith('OEBPS/Text/') and name.endswith('.xhtml'):
                with z.open(name) as f:
                    content = f.read()
                    soup = BeautifulSoup(content, 'xml')
                    text = soup.get_text(separator=' ', strip=True)

                    if len(text) < 50:
                        continue

                    chapter_idx += 1
                    doc = Document(
                        page_content=text,
                        metadata={
                            "source": "epub",
                            "book_title": title,
                            "chapter": chapter_idx,
                            "epub_file": epub_path.name
                        }
                    )
                    documents.append(doc)

    return documents


def load_epub_books(epub_dir: Path, force_recreate: bool = False) -> List[Document]:
    """
    加载并解析 epub_dir 目录下的所有 epub 文件

    Args:
        epub_dir: 包含 epub 文件的目录
        force_recreate: 是否强制重新解析

    Returns:
        Document 对象列表
    """
    epub_dir = Path(epub_dir)
    cache_file = CHUNKS_DIR / "epub_chunks.json"

    # 如果已有缓存且不强制重建，直接加载
    if cache_file.exists() and not force_recreate:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [Document(page_content=item['content'], metadata=item['metadata']) for item in data]

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    all_docs = []
    epub_files = list(epub_dir.glob("*.epub"))

    if not epub_files:
        print(f"警告: 在 {epub_dir} 中未找到 epub 文件")
        return []

    for epub_path in epub_files:
        try:
            docs = parse_epub(epub_path)
            print(f"解析 {epub_path.name}: {len(docs)} 个片段")
            all_docs.extend(docs)
        except Exception as e:
            print(f"解析 {epub_path.name} 失败: {e}，尝试使用备用方法...")
            try:
                docs = parse_epub_from_zip(epub_path)
                print(f"备用方法解析 {epub_path.name}: {len(docs)} 个片段")
                all_docs.extend(docs)
            except Exception as e2:
                print(f"备用方法也失败: {e2}")

    # 保存到缓存
    data = [{"content": doc.page_content, "metadata": doc.metadata} for doc in all_docs]
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"epub 解析完成，共 {len(all_docs)} 个片段，已缓存到 {cache_file}")
    return all_docs


if __name__ == "__main__":
    # 快速测试
    from pathlib import Path
    docs = load_epub_books(Path("epub_data"), force_recreate=True)
    print(f"\n共解析 {len(docs)} 个文档片段")
    for d in docs[:3]:
        print(f"  [{d.metadata.get('book_title')}] {d.page_content[:80]}...")
