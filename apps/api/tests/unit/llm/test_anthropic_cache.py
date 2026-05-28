"""Unit tests for AnthropicAdapter prompt-caching behaviour.

Mock-based — no VCR cassette, no real API calls. Tests that:
  - cache_system_prompt=True wraps system param as a list with cache_control
  - cache_system_prompt absent leaves system as a plain string
  - cache_read_input_tokens and cache_creation_input_tokens surface in LLMResponse
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from travel_agent.llm.anthropic import AnthropicAdapter
from travel_agent.llm.base import Message


_MSG = [Message(role="user", content="fly me to Tokyo")]


def _mock_response(
    cache_read: int = 0,
    cache_write: int = 0,
) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(type="text", text="ok")]
    resp.model = "claude-haiku-4-5-20251001"
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 20
    resp.usage.cache_read_input_tokens = cache_read
    resp.usage.cache_creation_input_tokens = cache_write
    return resp


async def test_cache_system_prompt_wraps_system_as_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache_system_prompt=True wraps system string in a list block with ephemeral cache_control."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    adapter = AnthropicAdapter()

    mock_create = AsyncMock(return_value=_mock_response())
    with patch.object(adapter._client.messages, "create", mock_create):
        await adapter.chat(
            _MSG,
            model="claude-haiku-4-5-20251001",
            system="You are a travel assistant.",
            cache_system_prompt=True,
        )

    call_kwargs = mock_create.call_args.kwargs
    system_param = call_kwargs["system"]
    assert isinstance(system_param, list), "system should be wrapped in a list"
    assert len(system_param) == 1
    assert system_param[0]["type"] == "text"
    assert system_param[0]["text"] == "You are a travel assistant."
    assert system_param[0]["cache_control"] == {"type": "ephemeral"}


async def test_no_cache_flag_passes_system_as_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without cache_system_prompt=True, system is passed as a plain string."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    adapter = AnthropicAdapter()

    mock_create = AsyncMock(return_value=_mock_response())
    with patch.object(adapter._client.messages, "create", mock_create):
        await adapter.chat(
            _MSG,
            model="claude-haiku-4-5-20251001",
            system="You are a travel assistant.",
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["system"] == "You are a travel assistant."


async def test_cache_tokens_surfaced_in_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache_read and cache_creation token counts are forwarded into LLMResponse."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    adapter = AnthropicAdapter()

    mock_create = AsyncMock(return_value=_mock_response(cache_read=512, cache_write=1024))
    with patch.object(adapter._client.messages, "create", mock_create):
        response = await adapter.chat(
            _MSG,
            model="claude-haiku-4-5-20251001",
            system="You are a travel assistant.",
            cache_system_prompt=True,
        )

    assert response.cache_read_input_tokens == 512
    assert response.cache_creation_input_tokens == 1024


async def test_cache_tokens_zero_when_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache token fields are 0 when the API returns 0 (prefix below threshold)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    adapter = AnthropicAdapter()

    mock_create = AsyncMock(return_value=_mock_response(cache_read=0, cache_write=0))
    with patch.object(adapter._client.messages, "create", mock_create):
        response = await adapter.chat(
            _MSG,
            model="claude-haiku-4-5-20251001",
            system="Short system prompt.",
            cache_system_prompt=True,
        )

    assert response.cache_read_input_tokens == 0
    assert response.cache_creation_input_tokens == 0
