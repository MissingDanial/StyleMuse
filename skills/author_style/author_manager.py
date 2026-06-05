"""
通用作家风格仿写 Skill - 作家管理模块

负责作家工作空间的创建、删除、列表等操作。
"""

import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional

from .config import AUTHORS_DIR, get_author_dir, save_author_config, get_llm
from .loader import load_all_chunks
from .analyzer import analyze_author
from .logger import get_logger

log = get_logger(__name__)


def list_authors() -> List[Dict[str, str]]:
    """
    列出所有已创建的作家。

    Returns:
        作家信息列表 [{"name": ..., "style_guide": ..., "has_vector_store": ...}, ...]
    """
    if not AUTHORS_DIR.exists():
        return []

    authors = []
    for author_dir in sorted(AUTHORS_DIR.iterdir()):
        if not author_dir.is_dir():
            continue

        name = author_dir.name
        has_style_guide = (author_dir / "style_guide.md").exists()
        has_vector_store = (author_dir / "cache" / "faiss" / "index.faiss").exists()

        # 统计文件数
        works_count = len(list((author_dir / "works").glob("*.txt"))) if (author_dir / "works").exists() else 0
        epub_count = len(list((author_dir / "epub").glob("*.epub"))) if (author_dir / "epub").exists() else 0

        authors.append({
            "name": name,
            "has_style_guide": has_style_guide,
            "has_vector_store": has_vector_store,
            "txt_files": works_count,
            "epub_files": epub_count,
        })

    return authors


def create_author(
    name: str,
    source_path: str = None,
    analyze: bool = True,
    build_index: bool = True,
    extra_config: dict = None,
) -> Path:
    """
    创建新的作家工作空间。

    流程：
    1. 创建目录结构
    2. 复制源文件（txt/epub）
    3. 分析写作风格（生成 style_guide.md）
    4. 构建向量索引

    Args:
        name: 作家名称（用作目录名，建议英文或拼音）
        source_path: 源文件路径（目录或单个文件）
        analyze: 是否自动分析风格
        build_index: 是否自动构建向量索引
        extra_config: 额外配置覆盖

    Returns:
        作家工作空间路径
    """
    author_dir = get_author_dir(name)

    if author_dir.exists():
        log.info(f"作家 '{name}' 已存在，将更新而非新建")

    # 创建目录结构
    (author_dir / "works").mkdir(parents=True, exist_ok=True)
    (author_dir / "epub").mkdir(parents=True, exist_ok=True)
    (author_dir / "cache").mkdir(parents=True, exist_ok=True)

    # 保存配置
    config = {"name": name}
    if extra_config:
        config.update(extra_config)
    save_author_config(name, config)

    # 复制源文件
    if source_path:
        source = Path(source_path)
        if source.is_dir():
            _copy_source_dir(source, author_dir)
        elif source.is_file():
            _copy_source_file(source, author_dir)
        else:
            log.warning(f"警告: 源路径不存在: {source_path}")

    # 统计文件
    works_dir = author_dir / "works"
    epub_dir = author_dir / "epub"
    txt_count = len(list(works_dir.glob("*.txt")))
    epub_count = len(list(epub_dir.glob("*.epub")))
    log.info(f"作家 '{name}' 已创建: {txt_count} 个 txt, {epub_count} 个 epub")

    # 分析风格
    if analyze and (txt_count > 0 or epub_count > 0):
        log.info("正在分析写作风格...")
        from .config import load_author_config
        cfg = load_author_config(name)
        documents = load_all_chunks(
            author_dir,
            chunk_size=cfg["chunk_size"],
            chunk_overlap=cfg["chunk_overlap"],
        )
        if documents:
            # 尝试使用 LLM 分析
            llm = None
            try:
                llm_cfg = dict(cfg)
                llm_cfg["max_tokens"] = 4000
                llm_cfg["temperature"] = 0.7
                llm = get_llm(llm_cfg)
            except Exception as e:
                log.error(f"  LLM 初始化失败，仅生成基础统计: {e}")

            analyze_author(name, documents, llm=llm, output_dir=author_dir)
        else:
            log.warning("  无文档可分析")

    # 构建向量索引
    if build_index and (txt_count > 0 or epub_count > 0):
        _build_vector_index(name, author_dir)

    return author_dir


def _copy_source_dir(source_dir: Path, author_dir: Path):
    """从源目录复制文件到作家工作空间"""
    # 复制 txt 文件
    for txt_file in source_dir.glob("**/*.txt"):
        dest = author_dir / "works" / txt_file.name
        if not dest.exists():
            shutil.copy2(txt_file, dest)
            log.info(f"  复制: {txt_file.name}")

    # 复制 epub 文件
    for epub_file in source_dir.glob("**/*.epub"):
        dest = author_dir / "epub" / epub_file.name
        if not dest.exists():
            shutil.copy2(epub_file, dest)
            log.info(f"  复制: {epub_file.name}")


def _copy_source_file(source_file: Path, author_dir: Path):
    """复制单个源文件"""
    if source_file.suffix.lower() == ".epub":
        dest = author_dir / "epub" / source_file.name
        if not dest.exists():
            shutil.copy2(source_file, dest)
            log.info(f"  复制: {source_file.name}")
    elif source_file.suffix.lower() == ".txt":
        dest = author_dir / "works" / source_file.name
        if not dest.exists():
            shutil.copy2(source_file, dest)
            log.info(f"  复制: {source_file.name}")
    else:
        log.warning(f"  跳过不支持的文件类型: {source_file.suffix}")


def _build_vector_index(name: str, author_dir: Path):
    """为作家构建向量索引"""
    from .config import load_author_config, get_embeddings
    from .rag_chain import create_vector_store

    cfg = load_author_config(name)
    cache_dir = author_dir / "cache" / "faiss"

    if (cache_dir / "index.faiss").exists():
        log.info("  向量索引已存在，跳过构建")
        return

    try:
        embeddings = get_embeddings(cfg)
    except Exception as e:
        log.error(f"  跳过向量索引构建: {e}")
        return

    log.info("  正在构建向量索引...")
    documents = load_all_chunks(
        author_dir,
        chunk_size=cfg["chunk_size"],
        chunk_overlap=cfg["chunk_overlap"],
        force_recreate=True,
    )

    if documents:
        create_vector_store(documents, embeddings, cache_dir)
        log.info(f"  向量索引构建完成: {len(documents)} 个文本块")
    else:
        log.warning("  无文档可索引")


def delete_author(name: str, confirm: bool = True) -> bool:
    """
    删除作家工作空间。

    Args:
        name: 作家名称
        confirm: 是否需要确认

    Returns:
        是否成功删除
    """
    author_dir = get_author_dir(name)
    if not author_dir.exists():
        log.warning(f"作家 '{name}' 不存在")
        return False

    if confirm:
        answer = input(f"确认删除作家 '{name}' 及其所有数据? (y/N): ")
        if answer.lower() != "y":
            log.info("已取消")
            return False

    shutil.rmtree(author_dir)
    log.info(f"已删除作家 '{name}'")
    return True


def get_author_info(name: str) -> Optional[Dict]:
    """获取作家详细信息"""
    author_dir = get_author_dir(name)
    if not author_dir.exists():
        return None

    works_dir = author_dir / "works"
    epub_dir = author_dir / "epub"
    cache_dir = author_dir / "cache"

    return {
        "name": name,
        "dir": str(author_dir),
        "txt_files": [f.name for f in works_dir.glob("*.txt")] if works_dir.exists() else [],
        "epub_files": [f.name for f in epub_dir.glob("*.epub")] if epub_dir.exists() else [],
        "has_style_guide": (author_dir / "style_guide.md").exists(),
        "has_few_shot": (author_dir / "few_shot.md").exists(),
        "has_vector_store": (cache_dir / "faiss" / "index.faiss").exists(),
        "config_file": str(author_dir / "config.json"),
    }
