"""
Tests for LLM call logging hooks.
"""

import logging

from skills.author_style.llm_logger import invoke_with_logging, stream_with_logging


class FakeResponse:
    def __init__(self, content, response_metadata=None, usage_metadata=None):
        self.content = content
        self.response_metadata = response_metadata or {}
        self.usage_metadata = usage_metadata or {}


class FakeLLM:
    def invoke(self, messages):
        assert messages
        return FakeResponse(
            "generated text",
            response_metadata={"model_name": "fake-model"},
            usage_metadata={"input_tokens": 3, "output_tokens": 2},
        )


class FakeStreamingLLM:
    def stream(self, messages):
        assert messages
        yield FakeResponse("hello ")
        yield FakeResponse("world")


def test_invoke_with_logging_records_response_without_sensitive_metadata(caplog):
    logger = logging.getLogger("tests.llm_logger.invoke")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    response = invoke_with_logging(
        FakeLLM(),
        ["prompt text"],
        step="unit_invoke",
        logger=logger,
        metadata={"author": "test", "llm_api_key": "secret-value"},
    )

    assert response.content == "generated text"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "unit_invoke" in messages
    assert "generated text" in messages
    assert "fake-model" in messages
    assert "secret-value" not in messages
    assert "'llm_api_key': '***'" in messages


def test_stream_with_logging_records_chunk_summary(caplog):
    logger = logging.getLogger("tests.llm_logger.stream")
    caplog.set_level(logging.DEBUG, logger=logger.name)

    chunks = list(
        stream_with_logging(
            FakeStreamingLLM(),
            ["prompt text"],
            step="unit_stream",
            logger=logger,
            metadata={"topic": "stream-test"},
        )
    )

    assert [chunk.content for chunk in chunks] == ["hello ", "world"]
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "unit_stream" in messages
    assert "chunks=2" in messages
    assert "chars=11" in messages
    assert "hello world" in messages
