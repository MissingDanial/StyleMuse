"""Publish FAISS generations only after both index files are written."""
import json
import os
import re
import uuid
import pickle
import shutil
from pathlib import Path

from .cache import CACHE_VERSION, atomic_write_json, fingerprint
from .logger import get_logger

log = get_logger(__name__)


def vector_manifest(documents, config: dict) -> dict:
    return {
        "version": CACHE_VERSION,
        "documents": fingerprint([{ "content": doc.page_content, "metadata": doc.metadata } for doc in documents]),
        "chunk_size": config.get("chunk_size"),
        "chunk_overlap": config.get("chunk_overlap"),
        "embedding": {key: config.get(key) for key in (
            "embedding_provider", "embedding_model", "embedding_base_url", "embedding_dimensions", "embedding_query_instruction"
        )},
    }


def _read_generation(persist_dir: Path, manifest=None):
    try:
        pointer = json.loads((persist_dir / "manifest.json").read_text(encoding="utf-8"))
        directory = pointer["directory"]
        if not isinstance(directory, str) or not re.fullmatch(r"[0-9a-f]{32}", directory):
            return None
        if manifest is not None and pointer.get("manifest") != manifest:
            return None
        generation = persist_dir / directory
        if (generation / "index.faiss").is_file() and (generation / "index.pkl").is_file():
            return generation
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def has_vector_store(persist_dir: Path) -> bool:
    return _read_generation(Path(persist_dir)) is not None


def create_vector_store(documents, embeddings, persist_dir: Path, manifest=None):
    from langchain_community.vectorstores import FAISS
    persist_dir = Path(persist_dir).resolve()
    persist_dir.mkdir(parents=True, exist_ok=True)
    vector_store = FAISS.from_documents(documents, embeddings)
    generation_id = uuid.uuid4().hex
    # The active pointer stays intact if embedding or serialization fails.
    # Windows TemporaryDirectory uses a private ACL for mode 0700. Renaming that
    # directory publishes the same ACL, preventing other worker identities from
    # loading it. A UUID staging directory inherits the author's cache ACL.
    staging = persist_dir / (".building-" + uuid.uuid4().hex)
    staging.mkdir()
    destination = persist_dir / generation_id
    try:
        vector_store.save_local(str(staging))
        if staging.resolve().parent != persist_dir or destination.resolve().parent != persist_dir:
            raise ValueError("向量索引发布目录越界")
        os.replace(staging, destination)
    finally:
        if staging.exists():
            if staging.is_symlink() or staging.resolve().parent != persist_dir:
                raise ValueError("向量索引清理目录越界")
            shutil.rmtree(staging)
    atomic_write_json(persist_dir / "manifest.json", {"directory": generation_id,
                      "manifest": manifest, "dimensions": int(vector_store.index.d)})
    return vector_store


def load_vector_store(embeddings, persist_dir: Path, manifest=None):
    from langchain_community.vectorstores import FAISS
    generation = _read_generation(Path(persist_dir), manifest)
    if generation is None:
        return None
    try:
        # Only this application's locally generated cache is accepted.
        return FAISS.load_local(str(generation), embeddings, allow_dangerous_deserialization=True)
    except (OSError, ValueError, RuntimeError, EOFError, pickle.UnpicklingError) as exc:
        log.warning("向量索引损坏，将重建: %s", type(exc).__name__)
        return None
