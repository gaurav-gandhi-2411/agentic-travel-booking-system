"""Unit tests for FallbackLLMClient — hop ordering, retryable-vs-not, observability."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from travel_agent.llm.base import LLMError, LLMResponse, Message
from travel_agent.llm.fallback import AllProvidersExhaustedError, FallbackHop, FallbackLLMClient

_MSG = [Message(role="user", content="fly from Delhi to Dubai next month")]


class _FakeClient:
    """LLMClient test double — returns/raises each entry in *outcomes* in order."""

    def __init__(self, *outcomes: LLMResponse | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[Message], *, model: str, **kwargs: Any) -> LLMResponse:
        self.calls.append({"model": model, "messages": messages, **kwargs})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(model: str) -> LLMResponse:
    return LLMResponse(content="ok", model=model, input_tokens=10, output_tokens=5, latency_ms=1.0)


async def test_falls_back_on_429_and_returns_fallback_response() -> None:
    primary = _FakeClient(LLMError("rate limited", status_code=429))
    fallback_client = _FakeClient(_response("google/gemma-4-31b-it:free"))
    hop = FallbackHop(
        provider="openrouter", model="google/gemma-4-31b-it:free", client=fallback_client
    )
    client = FallbackLLMClient(primary, "groq", [hop])
    response = await client.chat(_MSG, model="llama-3.3-70b-versatile")
    assert response.model == "google/gemma-4-31b-it:free"
    assert len(primary.calls) == 1
    assert primary.calls[0]["model"] == "llama-3.3-70b-versatile"
    assert len(fallback_client.calls) == 1
    assert fallback_client.calls[0]["model"] == "google/gemma-4-31b-it:free"


async def test_preserves_tool_contract_across_hops() -> None:
    """messages/system/tools/max_tokens/temperature reach the fallback hop unchanged."""
    from travel_agent.llm.base import ToolDefinition

    tool = ToolDefinition(name="t", description="d", input_schema={"type": "object"})
    primary = _FakeClient(LLMError("rate limited", status_code=429))
    fb = _FakeClient(_response("google/gemma-4-31b-it:free"))
    client = FallbackLLMClient(
        primary, "groq", [FallbackHop(provider="openrouter", model="gemma", client=fb)]
    )
    await client.chat(
        _MSG,
        model="llama-3.3-70b-versatile",
        max_tokens=999,
        temperature=0.3,
        system="be helpful",
        tools=[tool],
    )
    call = fb.calls[0]
    assert call["messages"] == _MSG
    assert call["max_tokens"] == 999
    assert call["temperature"] == pytest.approx(0.3)
    assert call["system"] == "be helpful"
    assert call["tools"] == [tool]


async def test_non_retryable_400_does_not_fall_back() -> None:
    primary = _FakeClient(LLMError("bad request", status_code=400))
    fb = _FakeClient(_response("google/gemma-4-31b-it:free"))
    client = FallbackLLMClient(
        primary, "groq", [FallbackHop(provider="openrouter", model="gemma", client=fb)]
    )
    with pytest.raises(LLMError, match="bad request"):
        await client.chat(_MSG, model="llama-3.3-70b-versatile")
    assert len(fb.calls) == 0  # never reached


async def test_all_hops_exhausted_raises_and_captures_sentry_exception() -> None:
    primary = _FakeClient(LLMError("rate limited", status_code=429))
    fb = _FakeClient(LLMError("also rate limited", status_code=429))
    client = FallbackLLMClient(
        primary, "groq", [FallbackHop(provider="openrouter", model="gemma", client=fb)]
    )
    with (
        patch("sentry_sdk.capture_exception") as mock_capture,
        pytest.raises(AllProvidersExhaustedError, match="All 2 LLM providers exhausted"),
    ):
        await client.chat(_MSG, model="llama-3.3-70b-versatile")
    mock_capture.assert_called_once()


async def test_successful_fallback_sends_sentry_warning_message() -> None:
    primary = _FakeClient(LLMError("rate limited", status_code=429))
    fb = _FakeClient(_response("google/gemma-4-31b-it:free"))
    client = FallbackLLMClient(
        primary, "groq", [FallbackHop(provider="openrouter", model="gemma", client=fb)]
    )
    with patch("sentry_sdk.capture_message") as mock_capture:
        await client.chat(_MSG, model="llama-3.3-70b-versatile")
    mock_capture.assert_called_once()
    assert mock_capture.call_args.kwargs.get("level") == "warning"


async def test_timeout_style_error_falls_back() -> None:
    """retryable=True with no status_code (connection/timeout) also falls back."""
    primary = _FakeClient(LLMError("connection timed out", status_code=None, retryable=True))
    fb = _FakeClient(_response("google/gemma-4-31b-it:free"))
    client = FallbackLLMClient(
        primary, "groq", [FallbackHop(provider="openrouter", model="gemma", client=fb)]
    )
    response = await client.chat(_MSG, model="llama-3.3-70b-versatile")
    assert response.model == "google/gemma-4-31b-it:free"


async def test_primary_success_never_touches_fallback() -> None:
    primary = _FakeClient(_response("llama-3.3-70b-versatile"))
    fb = _FakeClient(_response("google/gemma-4-31b-it:free"))
    client = FallbackLLMClient(
        primary, "groq", [FallbackHop(provider="openrouter", model="gemma", client=fb)]
    )
    response = await client.chat(_MSG, model="llama-3.3-70b-versatile")
    assert response.model == "llama-3.3-70b-versatile"
    assert len(fb.calls) == 0


def test_requires_at_least_one_fallback_hop() -> None:
    with pytest.raises(ValueError, match="at least one fallback hop"):
        FallbackLLMClient(_FakeClient(), "groq", [])
