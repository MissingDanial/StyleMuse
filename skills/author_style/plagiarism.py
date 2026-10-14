"""Local text-reuse evidence; semantic and structural reuse are separate checks."""
import hashlib
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import List

from langchain_core.documents import Document


class _SubstringIndex:
    """Suffix automaton: build in O(M), find the longest match in O(N)."""
    def __init__(self, text: str):
        self.text = text
        self.transitions = [{}]
        self.links = [-1]
        self.lengths = [0]
        self.ends = [-1]
        last = 0
        for offset, char in enumerate(text):
            current = len(self.transitions)
            self.transitions.append({})
            self.lengths.append(self.lengths[last] + 1)
            self.links.append(0)
            self.ends.append(offset)
            parent = last
            while parent >= 0 and char not in self.transitions[parent]:
                self.transitions[parent][char] = current
                parent = self.links[parent]
            if parent >= 0:
                target = self.transitions[parent][char]
                if self.lengths[parent] + 1 == self.lengths[target]:
                    self.links[current] = target
                else:
                    clone = len(self.transitions)
                    self.transitions.append(self.transitions[target].copy())
                    self.lengths.append(self.lengths[parent] + 1)
                    self.links.append(self.links[target])
                    self.ends.append(self.ends[target])
                    while parent >= 0 and self.transitions[parent].get(char) == target:
                        self.transitions[parent][char] = clone
                        parent = self.links[parent]
                    self.links[current] = self.links[target] = clone
            last = current

    def longest_match(self, source: str) -> str:
        state = length = best = 0
        end = -1
        for char in source:
            while state and char not in self.transitions[state]:
                state = self.links[state]
                length = self.lengths[state]
            if char in self.transitions[state]:
                state = self.transitions[state][char]
                length += 1
                if length > best:
                    best, end = length, self.ends[state]
            else:
                length = 0
        return self.text[end - best + 1:end + 1] if best else ""

    def matching_spans(self, source: str, minimum: int = 8) -> list:
        return self.match_details(source, minimum)[1]

    def match_details(self, source: str, minimum: int = 8):
        """Return maximal runs with positions on both normalized texts."""
        state = length = best = 0
        best_end = -1
        runs = {}
        for offset, char in enumerate(source):
            while state and char not in self.transitions[state]:
                state = self.links[state]
                length = self.lengths[state]
            if char not in self.transitions[state]:
                length = 0
                continue
            state = self.transitions[state][char]
            length += 1
            if length > best:
                best, best_end = length, self.ends[state]
            if length >= minimum:
                generated_start = self.ends[state] - length + 1
                source_start = offset - length + 1
                # Repeated passages in a large source need one strongest
                # location per output start, rather than an unbounded list.
                previous = runs.get(generated_start)
                if previous is None or length > previous[2]:
                    runs[generated_start] = (generated_start, source_start, length)
        return (self.text[best_end - best + 1:best_end + 1] if best else ""), list(runs.values())


def longest_common_substring(s1: str, s2: str) -> str:
    return _SubstringIndex(s1).longest_match(s2)


def _normalize_with_map(text: str, ignore_punctuation: bool = False):
    """Keep original character spans even when NFKC expands a character."""
    result, positions = [], []
    offset = 0
    while offset < len(text):
        end = offset + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        for char in unicodedata.normalize("NFKC", text[offset:end]):
            if char.isspace() or (ignore_punctuation and unicodedata.category(char).startswith("P")):
                continue
            result.append(char)
            positions.append((offset, end))
        offset = end
    return "".join(result), positions


def _normalize(text: str) -> str:
    return _normalize_with_map(text or "")[0]


def _windows(text: str, size: int = 200):
    if len(text) <= size:
        return [text] if text else []
    result = [text[i:i + size] for i in range(0, len(text) - size + 1, size // 2)]
    if result[-1] != text[-size:]:
        result.append(text[-size:])
    return result


def _positioned_windows(text: str, size: int):
    if len(text) <= size:
        return [(0, text)] if text else []
    starts = list(range(0, len(text) - size + 1, size // 2))
    if starts[-1] != len(text) - size:
        starts.append(len(text) - size)
    return [(start, text[start:start + size]) for start in starts]


def _near_match(source: str, generated: str, window_cache: dict, threshold: float):
    # Compare similar-sized windows so a short near-copy at the end of a long
    # article is not diluted by hundreds of unrelated preceding characters.
    size = min(200, max(20, ((len(source) + 19) // 20) * 20))
    if size not in window_cache:
        window_cache[size] = [(start, window, Counter(window))
                              for start, window in _positioned_windows(generated, size)]
    generated_windows = window_cache[size]
    for source_start, source_window in _positioned_windows(source, size):
        counts = Counter(source_window)
        for generated_start, generated_window, generated_counts in generated_windows:
            denominator = len(source_window) + len(generated_window)
            # An upper bound: no qualifying SequenceMatcher ratio is filtered out.
            overlap = sum(min(count, generated_counts.get(char, 0)) for char, count in counts.items())
            if not denominator or 2 * overlap / denominator < threshold:
                continue
            matcher = SequenceMatcher(None, generated_window, source_window, autojunk=False)
            ratio = matcher.ratio()
            if ratio >= threshold:
                return generated_start, source_start, len(generated_window), len(source_window), ratio
    return None


def _near_similarity(source: str, generated: str, window_cache: dict, threshold: float) -> float:
    match = _near_match(source, generated, window_cache, threshold)
    return match[-1] if match else 0.0


def _evidence(doc, generated_raw, generated_map, source_map, gstart, sstart, glen, slen, kind, similarity):
    generated_start, generated_end = generated_map[gstart][0], generated_map[gstart + glen - 1][1]
    source_start, source_end = source_map[sstart][0], source_map[sstart + slen - 1][1]
    metadata = doc.metadata
    source_id = metadata.get("source_id") or hashlib.sha256(
        (str(metadata.get("filename", "")) + doc.page_content).encode("utf-8")).hexdigest()
    return {
        "kind": kind, "source_id": source_id,
        "title": metadata.get("title", metadata.get("book_title", "未知")),
        "filename": metadata.get("filename", metadata.get("epub_file", metadata.get("file", ""))),
        "source_type": metadata.get("source_type", "work"), "source_url": metadata.get("source_url", ""),
        "offset_basis": metadata.get("offset_basis", "document_unicode_characters"),
        "matched_characters": glen if kind != "near_text" else None, "similarity": round(similarity, 3),
        "generated": {"start": generated_start, "end": generated_end,
                      "text": generated_raw[generated_start:generated_end]},
        "source": {"start": metadata.get("source_start", 0) + source_start,
                   "end": metadata.get("source_start", 0) + source_end,
                   "local_start": source_start, "local_end": source_end,
                   "line": metadata.get("source_line", 1) + doc.page_content.count("\n", 0, source_start),
                   "text": doc.page_content[source_start:source_end]},
    }


def _coverage(generated: str, spans: list) -> dict:
    merged = []
    for start, end in sorted(set(spans)):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    matched = sum(len(re.sub(r"\s+", "", generated[start:end])) for start, end in merged)
    total = len(re.sub(r"\s+", "", generated))
    return {"matched_chars": matched, "total_chars": total,
            "ratio": round(matched / total, 4) if total else 0.0,
            "intervals": merged, "basis": "union_of_exact_spans_at_least_8_normalized_characters"}


def check_plagiarism(generated: str, source_documents: List[Document], max_common_len: int = 15,
                     similarity_threshold: float = 0.6) -> dict:
    if max_common_len < 0 or not 0 < similarity_threshold <= 1:
        raise ValueError("invalid plagiarism thresholds")
    raw = generated or ""
    normalized, generated_map = _normalize_with_map(raw)
    sources = [(doc, *_normalize_with_map(doc.page_content)) for doc in source_documents or []]
    sources = [(doc, source, positions) for doc, source, positions in sources if source]
    methods = {"exact_text": "not_checked", "punctuation_insensitive": "not_checked",
               "near_text": "not_checked", "semantic": "not_checked", "structure": "not_checked"}
    scope = {"source_count": len(sources), "source_types": sorted({
        doc.metadata.get("source_type", "work") for doc, _, _ in sources}),
        "coverage": "loaded_sources_only"}
    if not normalized or not sources:
        return {"status": "not_checked", "passed": None, "max_common": 0, "max_common_text": "",
                "originality_status": "not_checked", "evidence": [], "methods": methods,
                "scope": scope, "similar_docs": [], "warning": "缺少生成内容或可比较的原文语料，重复检测未完成"}
    indexes = [(False, normalized, generated_map, _SubstringIndex(normalized))]
    lexical, lexical_map = _normalize_with_map(raw, ignore_punctuation=True)
    if lexical:
        indexes.append((True, lexical, lexical_map, _SubstringIndex(lexical)))
    window_cache = {}
    max_common_text = ""
    similar_docs = []
    evidence, covered = [], []
    for doc, source, source_map in sources:
        lexical_source = [(char, position) for char, position in zip(source, source_map)
                          if not unicodedata.category(char).startswith("P")]
        for ignore_punctuation, gen, gen_map, index in indexes:
            if ignore_punctuation:
                current_source = "".join(char for char, _ in lexical_source)
                current_map = [position for _, position in lexical_source]
            else:
                current_source, current_map = source, source_map
            common, spans = index.match_details(current_source, minimum=max(1, min(8, max_common_len + 1)))
            if len(common) > len(max_common_text):
                max_common_text = common
            for gstart, sstart, size in spans:
                kind = "punctuation_insensitive" if ignore_punctuation else "exact"
                hit = _evidence(doc, raw, gen_map, current_map, gstart, sstart, size, size, kind, 1.0)
                evidence.append(hit)
                if size >= 8:
                    # A repeated phrase in the output counts at every location,
                    # while overlap across sources/channels counts only once.
                    phrase = gen[gstart:gstart + size]
                    occurrence = gen.find(phrase)
                    while occurrence >= 0:
                        covered.append((gen_map[occurrence][0], gen_map[occurrence + size - 1][1]))
                        occurrence = gen.find(phrase, occurrence + 1)
        near = _near_match(source, normalized, window_cache, similarity_threshold)
        if near:
            gstart, sstart, glen, slen, ratio = near
            hit = _evidence(doc, raw, generated_map, source_map, gstart, sstart, glen, slen, "near_text", ratio)
            evidence.append(hit)
            similar_docs.append({"title": hit["title"], "similarity": hit["similarity"],
                                 "snippet": hit["source"]["text"], "evidence": hit})
    max_common = len(max_common_text)
    coverage = _coverage(raw, covered)
    if max_common > max_common_len:
        originality_status, passed = "high_risk", False
        warning = f"检测到 {max_common} 字连续或忽略标点后的文字重合，超过告警阈值 {max_common_len} 字，请核对来源"
    elif similar_docs:
        originality_status, passed = "needs_review", None
        warning = f"有 {len(similar_docs)} 个来源存在近似文字片段，需要复核"
    elif len(coverage["intervals"]) >= 2 and coverage["matched_chars"] >= 24 and coverage["ratio"] >= 0.15:
        originality_status, passed = "needs_review", None
        warning = "多处短片段与来源重合，需要复核是否存在拼接复用"
    else:
        originality_status, passed = "low_risk", True
        warning = "未发现超过阈值的文字复用；语义与结构复用尚未检查"
    methods.update(exact_text="checked", punctuation_insensitive="checked" if lexical else "not_checked", near_text="checked")
    # Prefer substantial evidence, collapse duplicate raw spans across channels,
    # and disclose display truncation without truncating coverage computation.
    evidence.sort(key=lambda item: (item["matched_characters"] or 0, item["similarity"]), reverse=True)
    unique = {}
    for hit in evidence:
        key = (hit["source_id"], hit["generated"]["start"], hit["generated"]["end"],
               hit["source"]["start"], hit["source"]["end"])
        unique.setdefault(key, hit)
    evidence = []
    for hit in unique.values():
        redundant = False
        for prior in evidence:
            if prior["source_id"] != hit["source_id"] or prior["kind"] == "near_text":
                continue
            source_contained = prior["source"]["start"] <= hit["source"]["start"] and prior["source"]["end"] >= hit["source"]["end"]
            generated_contained = prior["generated"]["start"] <= hit["generated"]["start"] and prior["generated"]["end"] >= hit["generated"]["end"]
            generated_overlaps = prior["generated"]["start"] < hit["generated"]["end"] and hit["generated"]["start"] < prior["generated"]["end"]
            if source_contained and (generated_contained or (hit["kind"] == "near_text" and generated_overlaps)):
                redundant = True
                break
        if not redundant:
            evidence.append(hit)
    return {"status": "checked", "passed": passed, "max_common": max_common,
            "max_common_text": max_common_text, "similar_docs": similar_docs[:5], "warning": warning,
            "originality_status": originality_status, "evidence": evidence[:20],
            "evidence_count": len(evidence), "evidence_truncated": len(evidence) > 20,
            "methods": methods, "scope": scope, "coverage": coverage}
