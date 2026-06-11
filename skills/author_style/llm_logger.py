"""
Logging hooks for LangChain chat model calls.

The helpers keep LLM tracing consistent across writing, rewriting, review, and
author analysis without leaking API keys into logs.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Iterator


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "secret",
    "password",
    "authorization",
}


def invoke_with_logging(
    llm,
    messages,
    *,
    step: str,
    logger,
    metadata: dict | None = None,
    preview_chars: int = 1200,
):
    """Invoke a chat model and write structured request/response trace logs."""
    request_id = _request_id()
    safe_metadata = _scrub(metadata or {})
    started = time.perf_counter()

    logger.info("LLM call start id=%s step=%s", request_id, step)
    logger.debug(
        "LLM call request id=%s step=%s metadata=%s messages=%s",
        request_id,
        step,
        safe_metadata,
        _summarize_messages(messages, preview_chars),
    )

    try:
        response = llm.invoke(messages)
    except Exception:
        logger.exception(
            "LLM call failed id=%s step=%s elapsed_ms=%d",
            request_id,
            step,
            _elapsed_ms(started),
        )
        raise

    text = extract_response_text(getattr(response, "content", response))
    logger.info(
        "LLM call done id=%s step=%s chars=%d elapsed_ms=%d",
        request_id,
        step,
        len(text),
        _elapsed_ms(started),
    )
    logger.debug(
        "LLM call response id=%s step=%s response_metadata=%s usage_metadata=%s text_preview=%r",
        request_id,
        step,
        _scrub(getattr(response, "response_metadata", {}) or {}),
        _scrub(getattr(response, "usage_metadata", {}) or {}),
        _preview(text, preview_chars),
    )
    return response


def stream_with_logging(
    llm,
    messages,
    *,
    step: str,
    logger,
    metadata: dict | None = None,
    preview_chars: int = 1200,
) -> Iterator[Any]:
    """Stream a chat model response and log aggregate stream diagnostics."""
    request_id = _request_id()
    safe_metadata = _scrub(metadata or {})
    started = time.perf_counter()
    chunk_count = 0
    char_count = 0
    preview_parts: list[str] = []

    logger.info("LLM stream start id=%s step=%s", request_id, step)
    logger.debug(
        "LLM stream request id=%s step=%s metadata=%s messages=%s",
        request_id,
        step,
        safe_metadata,
        _summarize_messages(messages, preview_chars),
    )

    try:
        for chunk in llm.stream(messages):
            chunk_count += 1
            text = extract_response_text(getattr(chunk, "content", chunk))
            char_count += len(text)
            if text and sum(len(part) for part in preview_parts) < preview_chars:
                preview_parts.append(text)
            yield chunk
    except GeneratorExit:
        logger.warning(
            "LLM stream closed early id=%s step=%s chunks=%d chars=%d elapsed_ms=%d",
            request_id,
            step,
            chunk_count,
            char_count,
            _elapsed_ms(started),
        )
        raise
    except Exception:
        logger.exception(
            "LLM stream failed id=%s step=%s chunks=%d chars=%d elapsed_ms=%d",
            request_id,
            step,
            chunk_count,
            char_count,
            _elapsed_ms(started),
        )
        raise

    logger.info(
        "LLM stream done id=%s step=%s chunks=%d chars=%d elapsed_ms=%d",
        request_id,
        step,
        chunk_count,
        char_count,
        _elapsed_ms(started),
    )
    logger.debug(
        "LLM stream response id=%s step=%s text_preview=%r",
        request_id,
        step,
        _preview("".join(preview_parts), preview_chars),
    )


def extract_response_text(content) -> str:
    """Extract plain text from common LangChain response content shapes."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if hasattr(block, "type") and block.type == "text":
                text_parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "\n".join(text_parts)

    return str(content)


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _summarize_messages(messages, preview_chars: int) -> list[dict]:
    summary = []
    for index, message in enumerate(messages or []):
        content = getattr(message, "content", message)
        text = extract_response_text(content)
        summary.append(
            {
                "index": index,
                "type": type(message).__name__,
                "chars": len(text),
                "preview": _preview(text, preview_chars),
            }
        )
    return summary


def _preview(text: str, limit: int) -> str:
    compact = (text or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "...[truncated]"


def _scrub(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if _is_sensitive_key(lowered):
                result[key] = "***"
            else:
                result[key] = _scrub(item)
        return result
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item) for item in value)
    return value


def _is_sensitive_key(key: str) -> bool:
    return (
        key in SENSITIVE_KEYS
        or key.endswith("_key")
        or key.endswith("-key")
        or "api_key" in key
        or "access_token" in key
    )
