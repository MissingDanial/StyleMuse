"""
Independent review agent for generated author-style writing.
"""

import json
import re
from difflib import SequenceMatcher
from typing import List, Optional

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from .logger import get_logger
from .llm_logger import invoke_with_logging
from .review_prompt import build_review_prompt


log = get_logger(__name__)

LENGTH_TARGETS = {
    "short": (400, 500),
    "medium": (800, 1000),
    "long": (1500, 2000),
}


def review_article(
    *,
    author_name: str,
    topic: str,
    tone: str,
    length: str,
    article: str,
    source_documents: Optional[List[Document]] = None,
    style_guide: str = "",
    few_shot: str = "",
    plagiarism_result: Optional[dict] = None,
    config: Optional[dict] = None,
    llm=None,
) -> dict:
    """Review generated text with deterministic checks and optional LLM feedback."""
    cfg = config or {}
    source_documents = source_documents or []
    plagiarism_result = plagiarism_result or {
        "passed": None,
        "max_common": 0,
        "max_common_text": "",
        "similar_docs": [],
        "warning": "重复检测未完成或未启用",
    }

    requirement = _review_requirement(article=article, topic=topic, tone=tone, length=length)
    style = _review_style(article=article, source_documents=source_documents, style_guide=style_guide)
    plagiarism = _review_plagiarism(plagiarism_result)

    score = round(
        requirement["score"] * 0.35
        + style["score"] * 0.35
        + plagiarism["score"] * 0.30
    )
    decision = _decision(score, requirement, style, plagiarism)

    result = {
        "agent": "rule",
        "passed": decision == "pass",
        "score": score,
        "decision": decision,
        "requirement_status": requirement["status"],
        "originality_status": plagiarism["originality_status"],
        "requirement": requirement,
        "style": style,
        "plagiarism": plagiarism,
        "suggestions": _build_suggestions(requirement, style, plagiarism),
    }

    if cfg.get("review_llm_enabled"):
        result["rule_summary"] = {"score": score, "decision": decision}
        result["llm_review"] = _run_llm_review(
            llm=llm,
            author_name=author_name,
            topic=topic,
            tone=tone,
            length=length,
            article=article,
            style_guide=style_guide,
            few_shot=few_shot,
            plagiarism_result=plagiarism_result,
        )
        result["agent"] = "rule+llm"
        llm_review = result["llm_review"]
        if llm_review["ok"]:
            data = llm_review["data"]
            severity = {"pass": 0, "warn": 1, "fail": 2}
            result["decision"] = max(decision, data["decision"], key=severity.get)
            result["score"] = min(score, data["score"])
            if result["score"] < 60 or data.get("plagiarism", {}).get("risk") == "high":
                result["decision"] = "fail"
            elif result["score"] < 80 and result["decision"] == "pass":
                result["decision"] = "warn"
            result["suggestions"] = list(dict.fromkeys(data.get("suggestions", []) + result["suggestions"]))[:5]
        else:
            if decision == "pass":
                result["decision"] = "warn"
            result["suggestions"].insert(0, "语义审稿未完成，请人工复核或重新审稿")
        result["passed"] = result["decision"] == "pass"

    return result


def _review_requirement(article: str, topic: str, tone: str, length: str) -> dict:
    text_len = _content_length(article)
    min_len, max_len = LENGTH_TARGETS.get(length, LENGTH_TARGETS["medium"])
    issues = []
    score = 100

    if text_len == 0:
        return {"score": 0, "passed": False, "status": "fail", "issues": ["生成内容为空"],
                "checks": {"non_empty": False, "length": False},
                "metrics": {"chars": 0, "target_min": min_len, "target_max": max_len}}

    if text_len < min_len:
        score -= min(35, round((min_len - text_len) / max(min_len, 1) * 45))
        issues.append(f"篇幅偏短，当前约 {text_len} 字，目标约 {min_len}-{max_len} 字")
    elif text_len > max_len:
        score -= min(25, round((text_len - max_len) / max(max_len, 1) * 30))
        issues.append(f"篇幅偏长，当前约 {text_len} 字，目标约 {min_len}-{max_len} 字")

    topic_score = _topic_coverage(article, topic)
    if topic_score < 0.35:
        score -= 30
        issues.append("主题呼应不足，文章可能偏题")
    elif topic_score < 0.60:
        score -= 14
        issues.append("主题呼应较弱，建议强化核心意象或论点")

    return {
        "score": _clamp(score),
        "passed": min_len <= text_len <= max_len,
        "status": "pass" if min_len <= text_len <= max_len else "fail",
        "checks": {"non_empty": True, "length": min_len <= text_len <= max_len,
                   "topic_method": "lexical_heuristic", "tone": "not_checked"},
        "issues": issues,
        "metrics": {
            "chars": text_len,
            "target_min": min_len,
            "target_max": max_len,
            "topic_coverage": round(topic_score, 3),
        },
    }


def _review_style(article: str, source_documents: List[Document], style_guide: str) -> dict:
    if not article:
        return {
            "score": 0,
            "matched": [],
            "missing": ["生成内容为空，无法评估风格"],
            "metrics": {},
        }

    generated_stats = _text_stats(article)
    source_text = "\n".join(doc.page_content for doc in source_documents if doc.metadata.get("style_eligible", True))
    if not source_text:
        score = 65 if style_guide else 55
        return {
            "score": score,
            "matched": ["已基于风格指南做有限检查"] if style_guide else [],
            "missing": ["缺少可比较的原文语料，风格相似度只能粗略判断"],
            "metrics": generated_stats,
        }

    source_stats = _text_stats(source_text)
    score = 55
    matched = []
    missing = []

    sentence_delta = abs(generated_stats["avg_sentence_len"] - source_stats["avg_sentence_len"])
    if sentence_delta <= 8:
        score += 18
        matched.append("句长节奏接近语料均值")
    elif sentence_delta <= 18:
        score += 9
        matched.append("句长节奏有一定接近度")
    else:
        missing.append("句长节奏与语料差异较大")

    punct_similarity = _punctuation_similarity(generated_stats["punctuation"], source_stats["punctuation"])
    score += round(punct_similarity * 14)
    if punct_similarity >= 0.65:
        matched.append("标点和停顿习惯较接近")
    else:
        missing.append("标点和停顿习惯不够接近")

    lexical_similarity = SequenceMatcher(
        None,
        _char_profile(article),
        _char_profile(source_text),
    ).ratio()
    score += round(lexical_similarity * 13)
    if lexical_similarity >= 0.28:
        matched.append("高频字词气质有一定重合")
    else:
        missing.append("词汇气质与原文样本重合偏低")

    if style_guide:
        score += 5
        matched.append("已使用作家风格指南作为评审依据")

    return {
        "score": _clamp(score),
        "matched": matched[:5],
        "missing": missing[:5],
        "metrics": {
            "generated": generated_stats,
            "source": source_stats,
            "punctuation_similarity": round(punct_similarity, 3),
            "lexical_similarity": round(lexical_similarity, 3),
        },
    }


def _review_plagiarism(plagiarism_result: dict) -> dict:
    passed = plagiarism_result.get("passed")
    similar_docs = plagiarism_result.get("similar_docs") or []
    max_common = int(plagiarism_result.get("max_common") or 0)
    warning = plagiarism_result.get("warning") or ""
    originality_status = plagiarism_result.get("originality_status")

    if originality_status == "high_risk" or passed is False:
        score = 20
        risk = "high"
        issues = [warning or "检测到较长连续重复"]
    elif originality_status == "needs_review" or similar_docs:
        score = 68
        risk = "medium"
        issues = [warning or "存在整体相似度较高的原文片段"]
    elif passed is None:
        score = 60
        risk = "unknown"
        issues = [warning or "重复检测未完成"]
    else:
        score = 95
        risk = "low"
        issues = []

    return {
        "score": score,
        "risk": risk,
        "passed": passed,
        "originality_status": originality_status or {"high": "high_risk", "medium": "needs_review",
                                                       "unknown": "not_checked", "low": "low_risk"}[risk],
        "issues": issues,
        "max_common": max_common,
        "max_common_text": plagiarism_result.get("max_common_text", ""),
        "similar_docs": similar_docs[:5],
        "evidence": plagiarism_result.get("evidence", []),
        "coverage": plagiarism_result.get("coverage", {}),
        "scope": plagiarism_result.get("scope", {}),
        "methods": plagiarism_result.get("methods", {}),
    }


def _run_llm_review(**kwargs) -> dict:
    llm = kwargs.pop("llm")
    prompt = build_review_prompt(**kwargs)
    try:
        response = invoke_with_logging(
            llm,
            [HumanMessage(content=prompt)],
            step="review_llm",
            logger=log,
            metadata={
                "author": kwargs.get("author_name"),
                "topic": kwargs.get("topic"),
                "tone": kwargs.get("tone"),
                "length": kwargs.get("length"),
                "article_chars": len(kwargs.get("article") or ""),
            },
        )
        content = _extract_response_text(response.content)
        parsed = _parse_json_object(content)
        _validate_llm_review(parsed)
        return {"ok": True, "data": parsed}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _decision(score: int, requirement: dict, style: dict, plagiarism: dict) -> str:
    if plagiarism["risk"] == "high" or requirement.get("passed") is False or requirement["score"] < 55:
        return "fail"
    if score < 60:
        return "fail"
    if score < 80 or style["score"] < 70 or plagiarism["risk"] in {"medium", "unknown"}:
        return "warn"
    return "pass"


def _build_suggestions(requirement: dict, style: dict, plagiarism: dict) -> List[str]:
    suggestions = []
    if requirement["issues"]:
        suggestions.append("先修正主题、篇幅或语气等硬性写作要求")
    if style["missing"]:
        suggestions.append("根据风格缺口调整句式节奏、意象选择和叙事视角")
    if plagiarism["risk"] != "low":
        suggestions.append("重写与原文相似的片段，避免连续复用原句或近似结构")
    if not suggestions:
        suggestions.append("整体质量可接受，可做少量润色后使用")
    return suggestions


def _content_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _topic_coverage(article: str, topic: str) -> float:
    if not topic:
        return 1.0
    article_norm = re.sub(r"\s+", "", article or "").lower()
    topic_norm = re.sub(r"\s+", "", topic or "").lower()
    if topic_norm and topic_norm in article_norm:
        return 1.0

    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]+", topic_norm)
    if not tokens:
        chars = [char for char in topic_norm if char.strip()]
        if not chars:
            return 1.0
        return sum(1 for char in set(chars) if char in article_norm) / len(set(chars))

    hits = sum(1 for token in tokens if token in article_norm)
    return hits / len(tokens)


def _text_stats(text: str) -> dict:
    compact = re.sub(r"\s+", "", text or "")
    sentences = [item for item in re.split(r"[。！？!?；;]+", compact) if item]
    sentence_count = len(sentences)
    avg_sentence_len = (
        round(sum(len(sentence) for sentence in sentences) / sentence_count, 2)
        if sentence_count
        else 0
    )
    punctuation = {
        mark: compact.count(mark)
        for mark in ["，", "。", "；", "：", "！", "？", ",", ".", ";", "!", "?"]
    }
    return {
        "chars": len(compact),
        "sentence_count": sentence_count,
        "avg_sentence_len": avg_sentence_len,
        "punctuation": punctuation,
    }


def _punctuation_similarity(left: dict, right: dict) -> float:
    keys = set(left) | set(right)
    left_total = sum(left.values()) or 1
    right_total = sum(right.values()) or 1
    distance = 0.0
    for key in keys:
        distance += abs((left.get(key, 0) / left_total) - (right.get(key, 0) / right_total))
    return max(0.0, 1.0 - distance / 2)


def _char_profile(text: str, limit: int = 80) -> str:
    counts = {}
    for char in re.sub(r"\s+", "", text or ""):
        if re.match(r"[\w\u4e00-\u9fff]", char):
            counts[char] = counts.get(char, 0) + 1
    return "".join(char for char, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit])


def _extract_response_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if not match:
        raise ValueError("LLM review did not return a JSON object")
    return json.loads(match.group(0))


def _validate_llm_review(data: dict):
    if not isinstance(data, dict) or data.get("decision") not in {"pass", "warn", "fail"}:
        raise ValueError("invalid review decision")
    if type(data.get("score")) is not int or not 0 <= data["score"] <= 100:
        raise ValueError("invalid review score")
    if type(data.get("passed")) is not bool or data["passed"] != (data["decision"] == "pass"):
        raise ValueError("invalid review passed flag")
    for key in ("requirement", "style", "plagiarism"):
        section = data.get(key, {})
        if not isinstance(section, dict):
            raise ValueError(f"invalid review {key}")
        if "score" in section and (type(section["score"]) is not int or not 0 <= section["score"] <= 100):
            raise ValueError(f"invalid review {key} score")
        for field in ("issues", "matched", "missing", "notes"):
            values = section.get(field, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError(f"invalid review {key}.{field}")
    if "risk" in data.get("plagiarism", {}) and data["plagiarism"]["risk"] not in {"low", "medium", "high", "unknown"}:
        raise ValueError("invalid review plagiarism risk")
    suggestions = data.get("suggestions", [])
    if not isinstance(suggestions, list) or not all(isinstance(value, str) for value in suggestions):
        raise ValueError("invalid review suggestions")


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))
