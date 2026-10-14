"""Local symmetric BGE candidate retrieval with original sentence-span evidence.

Cosine scores identify candidates for human review, never plagiarism verdicts.
This index is independent of the author-style RAG index and its distance cutoff.
"""
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock

import numpy as np

from .cache import fingerprint
from .local_embeddings import LocalEmbeddings, MODEL_NAME

SEMANTIC_VERSION = 1
MAX_SOURCE_UNITS = 20000
_cache_lock = RLock()


def sentence_units(text: str, max_chars: int = 220, min_chars: int = 18) -> list:
    """Preserve original positions; include sentences and adjacent context."""
    spans = []
    for paragraph in re.finditer(r"[^\n]+(?:\n(?![ \t]*\n)[^\n]+)*", text):
        sentences = []
        for sentence in re.finditer(r"[^。！？!?；;]+[。！？!?；;]*", paragraph.group()):
            start, end = paragraph.start() + sentence.start(), paragraph.start() + sentence.end()
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start < end:
                sentences.append((start, end))
        for index, (start, end) in enumerate(sentences):
            if end - start > max_chars:
                for offset in range(start, end, max_chars - 60):
                    spans.append((offset, min(offset + max_chars, end)))
            else:
                spans.append((start, end))
            for following in range(index + 1, min(index + 4, len(sentences))):
                context_end = sentences[following][1]
                if context_end - start > max_chars:
                    break
                spans.append((start, context_end))
    return [{"start": start, "end": end, "text": text[start:end]}
            for start, end in sorted(set(spans)) if len(re.sub(r"\s+", "", text[start:end])) >= min_chars
            and any(char.isalnum() for char in text[start:end])]


def source_units(documents) -> list:
    units = []
    for doc in documents:
        metadata = doc.metadata
        source_id = metadata.get("source_id") or fingerprint({"metadata": metadata, "text": doc.page_content})
        for span in sentence_units(doc.page_content):
            units.append({**span, "source_id": source_id, "metadata": metadata,
                          "line": metadata.get("source_line", 1) + doc.page_content.count("\n", 0, span["start"])})
            if len(units) > MAX_SOURCE_UNITS:
                raise ValueError("语义来源片段超过 20000 条，请分批审核语料")
    return units


def _matrix(rows, count):
    matrix = np.asarray(rows, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != count or not matrix.shape[1] or not np.isfinite(matrix).all():
        raise ValueError("语义嵌入返回无效向量")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("语义嵌入返回零向量")
    return matrix / norms


class SemanticIndex:
    def __init__(self, documents, embeddings, cache_dir=None, identity=None):
        started = time.perf_counter()
        self.units = source_units(documents)
        if not self.units:
            raise ValueError("缺少足够长度的语义来源句段")
        self.embeddings = embeddings
        self.identity = identity or {"model": getattr(embeddings, "model", type(embeddings).__name__)}
        self.manifest = {"version": SEMANTIC_VERSION, "mode": "symmetric_no_instruction",
                         "embedding": self.identity, "units": fingerprint(self.units)}
        self.cache_hit = False
        cache = Path(cache_dir) / (fingerprint(self.manifest) + ".npz") if cache_dir else None
        with _cache_lock:
            matrix = None
            if cache and cache.is_file():
                try:
                    with np.load(cache, allow_pickle=False) as stored:
                        if str(stored["manifest"].item()) == fingerprint(self.manifest):
                            matrix = _matrix(stored["vectors"], len(self.units))
                            self.cache_hit = True
                except (OSError, ValueError, KeyError, EOFError):
                    matrix = None
            if matrix is None:
                matrix = _matrix(embeddings.embed_documents([unit["text"] for unit in self.units]), len(self.units))
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    descriptor, temporary = tempfile.mkstemp(prefix=".semantic-", dir=cache.parent)
                    try:
                        with os.fdopen(descriptor, "wb") as stream:
                            np.savez_compressed(stream, vectors=matrix, manifest=fingerprint(self.manifest))
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, cache)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
            self.vectors = matrix
        self.index_seconds = time.perf_counter() - started

    def query(self, generated, threshold=0.6, top_k=5, max_candidates=12):
        started = time.perf_counter()
        if not -1 <= threshold <= 1 or not 1 <= top_k <= 20 or not 1 <= max_candidates <= 50:
            raise ValueError("无效语义召回参数")
        units = sentence_units(generated)
        if not units:
            return {"status": "not_checked", "candidates": [], "warning": "生成内容缺少足够长度的语义句段"}
        # Disclose limits rather than quietly reporting a complete clean scan.
        checked_units = units[:128]
        vectors = _matrix(self.embeddings.embed_documents([unit["text"] for unit in checked_units]), len(checked_units))
        if vectors.shape[1] != self.vectors.shape[1]:
            raise ValueError("生成与来源语义向量维度不一致")
        similarities = vectors @ self.vectors.T
        found = []
        for row, generated_span in enumerate(checked_units):
            # Each source document gets its best aligned span, so overlapping
            # windows from one article do not occupy every retrieval position.
            per_source = {}
            for index in np.argsort(-similarities[row], kind="stable"):
                similarity = float(similarities[row, index])
                if similarity < threshold:
                    break
                source_span = self.units[int(index)]
                if source_span["source_id"] in per_source:
                    continue
                metadata = source_span["metadata"]
                source_start = metadata.get("source_start", 0) + source_span["start"]
                source_end = metadata.get("source_start", 0) + source_span["end"]
                hit = {"kind": "semantic_candidate", "source_id": source_span["source_id"],
                       "title": metadata.get("title", metadata.get("book_title", "未知")),
                       "filename": metadata.get("filename", metadata.get("epub_file", "")),
                       "source_url": metadata.get("source_url", ""), "source_type": metadata.get("source_type", "work"),
                       "offset_basis": metadata.get("offset_basis", "document_unicode_characters"),
                       "similarity": round(max(-1.0, min(1.0, similarity)), 6), "model": self.identity.get("model"),
                       "assessment": "pending", "generated": generated_span,
                       "source": {"start": source_start, "end": source_end, "local_start": source_span["start"],
                                  "local_end": source_span["end"], "line": source_span["line"], "text": source_span["text"]}}
                hit["evidence_id"] = fingerprint({key: hit[key] for key in
                                                 ("source_id", "generated", "source", "model", "kind")})
                per_source[source_span["source_id"]] = hit
                if len(per_source) == top_k:
                    break
            found.extend(per_source.values())
        found.sort(key=lambda hit: (-hit["similarity"], hit["source_id"], hit["generated"]["start"], -hit["generated"]["end"]))
        # Suppress contained alignments for the same source while preserving
        # separate passages that require separate review.
        unique = []
        for hit in found:
            if any(previous["source_id"] == hit["source_id"]
                   and previous["generated"]["start"] <= hit["generated"]["start"]
                   and previous["generated"]["end"] >= hit["generated"]["end"] for previous in unique):
                continue
            unique.append(hit)
        return {"status": "candidates_ready", "model": self.identity.get("model"),
                "mode": "symmetric_no_instruction", "threshold": threshold, "top_k": top_k,
                "candidates": unique[:max_candidates], "candidate_count": len(unique),
                "display_truncated": len(unique) > max_candidates,
                "generated_units": len(units), "checked_generated_units": len(checked_units),
                "scan_truncated": len(units) > len(checked_units), "source_units": len(self.units),
                "index_sha256": fingerprint(self.manifest), "cache_hit": self.cache_hit,
                "index_seconds": round(self.index_seconds, 4), "query_seconds": round(time.perf_counter() - started, 4),
                "adjudication": "pending" if unique else "no_candidates", "structure": "not_checked"}


def local_semantic_embeddings(config):
    """Always local, never use a remote provider or download during writing."""
    root = Path(__file__).resolve().parents[2]
    cache = Path(config.get("embedding_cache_dir") or ".stylemuse/models")
    if not cache.is_absolute():
        cache = root / cache
    return LocalEmbeddings(model=MODEL_NAME, cache_dir=cache, query_instruction="",
                           threads=config.get("embedding_threads", 2), local_files_only=True)


def load_profile(path=None):
    default = {"model": MODEL_NAME, "threshold": 0.6, "top_k": 5, "calibration": "uncalibrated"}
    if not path:
        return default
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if profile.get("model") != MODEL_NAME or profile.get("mode") != "symmetric_no_instruction":
        raise ValueError("语义校准配置与嵌入模型或表示方式不匹配")
    if profile.get("semantic_version") != SEMANTIC_VERSION:
        raise ValueError("语义校准配置与句段解析版本不匹配")
    if type(profile.get("threshold")) not in (int, float) or not -1 <= profile["threshold"] <= 1:
        raise ValueError("无效语义校准阈值")
    return profile


def merge_semantic_result(text_result, semantic):
    """Only human review can resolve a semantic candidate; keep text red flags."""
    text_result["semantic"] = semantic
    text_result["methods"]["semantic"] = "candidates_only" if semantic["status"] == "candidates_ready" else semantic["status"]
    candidates = semantic.get("candidates", [])
    text_result["evidence"] = text_result.get("evidence", [])[:8] + candidates
    text_result["evidence_count"] = text_result.get("evidence_count", 0) + semantic.get("candidate_count", 0)
    text_result["evidence_truncated"] = text_result.get("evidence_truncated", False) or semantic.get("display_truncated", False)
    text_result["methods"]["semantic_adjudication"] = "pending" if candidates else "not_checked"
    if text_result.get("originality_status") == "high_risk":
        return text_result
    if candidates or semantic.get("scan_truncated") or semantic["status"] != "candidates_ready":
        text_result.update(originality_status="needs_review", passed=None,
                           warning="语义候选需要核对具体表达与事件；相似分本身不代表复用" if candidates
                           else semantic.get("warning", "语义检查未完整完成，需要复核"))
    elif text_result.get("originality_status") == "low_risk":
        text_result["warning"] = "未发现超过设定阈值的文字或语义候选；情节结构尚未检查"
    return text_result
