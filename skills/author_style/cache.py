"""Content-addressed cache metadata and atomic file writes."""
import hashlib
import json
import os
import tempfile
import shutil
from pathlib import Path

CACHE_VERSION = 2


def fingerprint(value) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def file_manifest(directory: Path, suffix: str) -> list:
    result = []
    for path in sorted(Path(directory).glob(f"*{suffix}")):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            result.append({"file": path.name, "sha256": digest.hexdigest()})
    return result


def atomic_write_text(path: Path, text: str):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_write_json(path: Path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def atomic_copy(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as outgoing, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_cached_documents(path: Path, manifest: dict):
    from langchain_core.documents import Document
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("manifest") == manifest:
            return [Document(page_content=item["content"], metadata=item["metadata"])
                    for item in data["documents"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write_cached_documents(path: Path, manifest: dict, documents):
    atomic_write_json(path, {"manifest": manifest, "documents": [
        {"content": doc.page_content, "metadata": doc.metadata} for doc in documents
    ]})
