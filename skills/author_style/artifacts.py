"""Shared artifact persistence for Web and CLI generation."""
import json
from datetime import datetime, timezone
from pathlib import Path
from .cache import atomic_write_json


def save_review(article_path: Path, review: dict) -> Path | None:
    """Save the structured review report next to a generated article."""
    if not review:
        return None
    path = Path(article_path)
    review_path = path.with_suffix(".review.json")
    atomic_write_json(review_path, review)
    return review_path


def save_version_metadata(
    article_path: Path,
    *,
    kind: str,
    author: str,
    topic: str,
    article: str,
    review: dict | None,
    plagiarism: dict | None,
    parent_version: str = "",
    request_options: dict | None = None,
) -> Path:
    """Save version-chain metadata next to a generated article."""
    path = Path(article_path)
    review_path = path.with_suffix(".review.json") if review else None
    version_path = path.with_suffix(".version.json")
    metadata = {
        "version_id": path.stem,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "author": author,
        "topic": topic,
        "article_path": str(path),
        "review_path": str(review_path) if review_path else "",
        "parent_version": str(parent_version or ""),
        "article_chars": len(article or ""),
        "request": request_options or {},
        "review_summary": _summarize_review(review),
        "plagiarism_summary": _summarize_plagiarism(plagiarism),
    }
    atomic_write_json(version_path, metadata)
    return version_path


def _summarize_review(review: dict | None) -> dict:
    if not review:
        return {}
    requirement = review.get("requirement") or {}
    style = review.get("style") or {}
    plagiarism = review.get("plagiarism") or {}
    return {
        "decision": review.get("decision"),
        "passed": review.get("passed"),
        "score": review.get("score"),
        "requirement_score": requirement.get("score"),
        "requirement_status": review.get("requirement_status", requirement.get("status")),
        "originality_status": review.get("originality_status", plagiarism.get("originality_status")),
        "style_score": style.get("score"),
        "plagiarism_score": plagiarism.get("score"),
        "plagiarism_risk": plagiarism.get("risk"),
        "suggestion_count": len(review.get("suggestions") or []),
    }


def _summarize_plagiarism(plagiarism: dict | None) -> dict:
    if not plagiarism:
        return {}
    return {
        "passed": plagiarism.get("passed"),
        "originality_status": plagiarism.get("originality_status"),
        "coverage": plagiarism.get("coverage", {}),
        "methods": plagiarism.get("methods", {}),
        "scope": plagiarism.get("scope", {}),
        "max_common": plagiarism.get("max_common"),
        "similar_doc_count": len(plagiarism.get("similar_docs") or []),
        "warning": plagiarism.get("warning", ""),
    }
