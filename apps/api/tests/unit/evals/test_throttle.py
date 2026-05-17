"""Unit tests for optimizer eval throttle (TokenTracker + ThrottledLLMClient)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "evals"))

from optimizer.throttle import ThrottledLLMClient, TokenTracker
from travel_agent.llm.base import LLMError, LLMResponse, Message, ToolCall


def _response(input_tokens: int = 300, output_tokens: int = 50) -> LLMResponse:
    return LLMResponse(
        content="ok",
        model="test-model",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=10.0,
        tool_calls=[],
    )


def _msg() -> list[Message]:
    return [Message(role="user", content="hello")]


# ── TokenTracker ──────────────────────────────────────────────────────────────


def test_tracker_under_limit_returns_zero_wait() -> None:
    tracker = TokenTracker(tpm_limit=5_000)
    tracker.record(2_000)
    assert tracker.wait_seconds(2_000) == 0.0


def test_tracker_over_limit_returns_positive_wait() -> None:
    tracker = TokenTracker(tpm_limit=5_000)
    tracker.record(4_500)
    wait = tracker.wait_seconds(1_000)  # 4500 + 1000 > 5000
    assert wait > 0


def test_tracker_current_usage_matches_recorded() -> None:
    tracker = TokenTracker(tpm_limit=10_000)
    tracker.record(1_000)
    tracker.record(2_000)
    assert tracker.current_usage() == 3_000


def test_tracker_prunes_old_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Events older than window_s are pruned, freeing up capacity."""
    tracker = TokenTracker(tpm_limit=5_000, window_s=1.0)
    tracker.record(4_800)
    # fast-forward time past the 1s window
    original_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: original_monotonic() + 2.0)
    assert tracker.current_usage() == 0
    assert tracker.wait_seconds(4_800) == 0.0


def test_tracker_empty_returns_zero_wait() -> None:
    tracker = TokenTracker(tpm_limit=5_000)
    assert tracker.wait_seconds(600) == 0.0


def test_tracker_exact_limit_boundary() -> None:
    tracker = TokenTracker(tpm_limit=1_000)
    tracker.record(1_000)
    # At exactly the limit, adding any more should require wait
    assert tracker.wait_seconds(1) > 0


# ── ThrottledLLMClient ────────────────────────────────────────────────────────


async def test_throttled_client_passes_through_on_success() -> None:
    inner = AsyncMock()
    inner.chat = AsyncMock(return_value=_response(300, 50))
    tracker = TokenTracker(tpm_limit=5_000)

    client = ThrottledLLMClient(inner, tracker)
    resp = await client.chat(_msg(), model="test", max_tokens=256)

    assert resp.input_tokens == 300
    assert resp.output_tokens == 50
    inner.chat.assert_called_once()


async def test_throttled_client_records_tokens_after_call() -> None:
    inner = AsyncMock()
    inner.chat = AsyncMock(return_value=_response(300, 50))
    tracker = TokenTracker(tpm_limit=5_000)

    client = ThrottledLLMClient(inner, tracker)
    await client.chat(_msg(), model="test", max_tokens=256)

    assert tracker.current_usage() == 350  # 300 + 50


async def test_throttled_client_sleeps_when_near_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = AsyncMock()
    inner.chat = AsyncMock(return_value=_response(10, 10))

    # Pre-fill tracker so next call would exceed limit
    tracker = TokenTracker(tpm_limit=500)
    tracker.record(450)  # 450 + 600 estimate > 500 → should sleep

    sleep_calls: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    monkeypatch.setattr("optimizer.throttle.asyncio.sleep", fake_sleep)

    client = ThrottledLLMClient(inner, tracker)
    await client.chat(_msg(), model="test", max_tokens=256)

    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0


async def test_throttled_client_no_sleep_when_under_limit() -> None:
    inner = AsyncMock()
    inner.chat = AsyncMock(return_value=_response(10, 10))
    tracker = TokenTracker(tpm_limit=5_000)
    # No records — well under limit

    sleep_called = False

    async def fake_sleep(s: float) -> None:
        nonlocal sleep_called
        sleep_called = True

    client = ThrottledLLMClient(inner, tracker)
    await client.chat(_msg(), model="test", max_tokens=256)

    assert not sleep_called


async def test_throttled_client_falls_back_on_429() -> None:
    inner = AsyncMock()
    inner.chat = AsyncMock(side_effect=LLMError("429 Too Many Requests"))

    fallback = AsyncMock()
    fallback.chat = AsyncMock(return_value=_response(250, 60))

    tracker = TokenTracker(tpm_limit=5_000)
    client = ThrottledLLMClient(
        inner, tracker, fallback=fallback, fallback_model="deepseek-ai/deepseek-v4-pro"
    )
    resp = await client.chat(_msg(), model="llama-3.3-70b-versatile", max_tokens=256)

    assert resp.input_tokens == 250
    fallback.chat.assert_called_once()
    # fallback is called with fallback_model, not original model
    call_kwargs = fallback.chat.call_args
    assert call_kwargs.kwargs["model"] == "deepseek-ai/deepseek-v4-pro"


async def test_throttled_client_reraises_non_429_errors() -> None:
    inner = AsyncMock()
    inner.chat = AsyncMock(side_effect=LLMError("503 Service Unavailable"))
    tracker = TokenTracker(tpm_limit=5_000)
    client = ThrottledLLMClient(inner, tracker)

    with pytest.raises(LLMError, match="503"):
        await client.chat(_msg(), model="test", max_tokens=256)


async def test_throttled_client_reraises_429_when_no_fallback() -> None:
    inner = AsyncMock()
    inner.chat = AsyncMock(side_effect=LLMError("429 Too Many Requests"))
    tracker = TokenTracker(tpm_limit=5_000)
    client = ThrottledLLMClient(inner, tracker)  # no fallback configured

    with pytest.raises(LLMError, match="429"):
        await client.chat(_msg(), model="test", max_tokens=256)


async def test_throttled_client_fallback_records_tokens() -> None:
    """Tokens from fallback response are recorded in the tracker."""
    inner = AsyncMock()
    inner.chat = AsyncMock(side_effect=LLMError("429 Too Many Requests"))
    fallback = AsyncMock()
    fallback.chat = AsyncMock(return_value=_response(250, 60))

    tracker = TokenTracker(tpm_limit=5_000)
    client = ThrottledLLMClient(
        inner, tracker, fallback=fallback, fallback_model="deepseek-ai/deepseek-v4-pro"
    )
    await client.chat(_msg(), model="test", max_tokens=256)

    assert tracker.current_usage() == 310  # 250 + 60
