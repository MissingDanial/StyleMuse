"""
Path and deletion safety helpers for author workspaces.
"""

import re
from pathlib import Path


WINDOWS_RESERVED_CHARS = '<>:"/\\|?*'
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_simple_name(value: str, field_name: str = "name") -> str:
    """Validate a user-facing name used as one path segment."""
    name = (value or "").strip()
    if not name:
        raise ValueError(f"{field_name} 不能为空")
    if name in {".", ".."}:
        raise ValueError(f"{field_name} 不能是 {name!r}")
    if any(ch in name for ch in ("/", "\\")) or Path(name).name != name:
        raise ValueError(f"{field_name} 不能包含路径分隔符")
    if CONTROL_CHARS_RE.search(name):
        raise ValueError(f"{field_name} 不能包含控制字符")
    return name


def safe_filename(value: str, default: str = "untitled", suffix: str = "") -> str:
    """Return a filesystem-safe filename while preserving Chinese text."""
    raw_name = Path(value or "").name
    stem = raw_name
    if suffix and stem.lower().endswith(suffix.lower()):
        stem = stem[: -len(suffix)]
    stem = CONTROL_CHARS_RE.sub("_", stem)
    for char in WINDOWS_RESERVED_CHARS:
        stem = stem.replace(char, "_")
    stem = stem.strip(" ._")
    if not stem:
        stem = default
    if len(stem) > 120:
        stem = stem[:120].rstrip(" ._") or default
    return f"{stem}{suffix}"


def unique_path(directory: Path, filename: str) -> Path:
    """Return a non-conflicting file path in a directory."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    for i in range(2, 1000):
        next_candidate = directory / f"{stem}_{i}{suffix}"
        if not next_candidate.exists():
            return next_candidate
    raise ValueError("could not generate a unique filename")


def resolve_under_base(base_dir: Path, candidate: Path, field_name: str = "path") -> Path:
    """Resolve a path and ensure it remains inside the provided base directory."""
    base = base_dir.resolve()
    resolved = candidate.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(f"{field_name} 必须位于项目目录内: {base}")
    return resolved


def count_files(path: Path) -> int:
    """Count files under a file or directory."""
    if path.is_file():
        return 1
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())
