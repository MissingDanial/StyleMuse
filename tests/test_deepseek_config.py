"""Provider options must not leak into unrelated OpenAI-compatible endpoints."""
from unittest.mock import patch

import pytest

from skills.author_style.config import get_llm


@pytest.mark.parametrize("base_url", ["https://api.deepseek.com", "https://api.deepseek.com/v1"])
def test_deepseek_creative_writing_disables_thinking_and_records_stream_usage(base_url):
    with patch("langchain_openai.ChatOpenAI") as client:
        get_llm({"llm_api_key": "test-key", "llm_base_url": base_url, "llm_model": "deepseek-flash"})
    assert client.call_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert client.call_args.kwargs["stream_usage"] is True


def test_deepseek_thinking_can_be_enabled_explicitly():
    with patch("langchain_openai.ChatOpenAI") as client:
        get_llm({"llm_api_key": "test-key", "llm_base_url": "https://api.deepseek.com", "deepseek_thinking": "enabled"})
    assert client.call_args.kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


@pytest.mark.parametrize("base_url", ["", "https://example.com/v1", "https://api.deepseek.com.example.com/v1"])
def test_other_providers_do_not_receive_deepseek_options(base_url):
    with patch("langchain_openai.ChatOpenAI") as client:
        get_llm({"llm_api_key": "test-key", "llm_base_url": base_url})
    assert "extra_body" not in client.call_args.kwargs
    assert "stream_usage" not in client.call_args.kwargs


def test_invalid_thinking_setting_rejected_before_calling_provider():
    with pytest.raises(ValueError, match="deepseek_thinking"):
        get_llm({"llm_api_key": "test-key", "llm_base_url": "https://api.deepseek.com", "deepseek_thinking": "yes"})
