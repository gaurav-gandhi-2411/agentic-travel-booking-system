"""Token-rate throttle for the optimizer eval runner.

Prevents Groq TPM quota exhaustion (Issue #15) by tracking actual token usage
in a 60-second sliding window and sleeping before calls that would exceed the limit.
On a 429 rate-limit error, retries once with the configured NIM fallback client.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

import structlog

from travel_agent.llm.base import LLMError, LLMResponse, Message, ToolDefinition

_logger = structlog.get_logger(__name__)

# Conservative TPM limits per provider — below actual free-tier caps to leave headroom.
# Groq free tier: 6000 TPM for llama-3.3-70b-versatile; use 5000 for buffer.
TPM_LIMITS: dict[str, int] = {
    "groq": 5_000,
}

# Token estimate used as a pre-call reservation.
# Actual usage is recorded after each call; this estimate prevents burst over-shoots.
# 600 = conservative ceiling (max_tokens=256 output + ~350 typical input).
_CALL_TOKEN_ESTIMATE = 600


class TokenTracker:
    """Sliding-window actual-token tracker. Records usage; computes wait times."""

    def __init__(self, tpm_limit: int, window_s: float = 60.0) -> None:
        self._limit = tpm_limit
        self._window = window_s
        self._events: deque[tuple[float, int]] = deque()

    def record(self, tokens: int) -> None:
        """Record actual tokens consumed by a completed call."""
        self._events.append((time.monotonic(), tokens))

    def _prune(self) -> None:
        cutoff = time.monotonic() - self._window
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def current_usage(self) -> int:
        """Tokens consumed in the current sliding window."""
        self._prune()
        return sum(t for _, t in self._events)

    def wait_seconds(self, estimated_next: int) -> float:
        """Seconds to sleep before issuing a call estimated at ~estimated_next tokens.

        Returns 0.0 if issuing now would stay within the limit.
        Otherwise computes how long to wait until enough past usage ages out.
        """
        self._prune()
        current = sum(t for _, t in self._events)
        if current + estimated_next <= self._limit:
            return 0.0
        needed_to_free = (current + estimated_next) - self._limit
        freed = 0
        for ts, tok in self._events:
            freed += tok
            if freed >= needed_to_free:
                wait = (ts + self._window) - time.monotonic() + 0.5
                return max(0.0, wait)
        return self._window  # fallback: wait full window


class ThrottledLLMClient:
    """LLM client wrapper that enforces TPM rate limits and falls back to NIM on 429.

    Before each call, checks whether issuing the call would exceed the sliding-window
    TPM limit and sleeps if needed. On a 429 rate-limit error, retries once with
    the configured NIM fallback client/model.
    """

    def __init__(
        self,
        inner: Any,
        tracker: TokenTracker,
        *,
        fallback: Any | None = None,
        fallback_model: str = "",
    ) -> None:
        self._inner = inner
        self._tracker = tracker
        self._fallback = fallback
        self._fallback_model = fallback_model

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        wait = self._tracker.wait_seconds(_CALL_TOKEN_ESTIMATE)
        if wait > 0:
            _logger.info("rate_limit_throttle", wait_s=round(wait, 1), provider="groq")
            await asyncio.sleep(wait)

        try:
            response = await self._inner.chat(
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                tools=tools,
                **kwargs,
            )
        except LLMError as exc:
            if "429" in str(exc) and self._fallback and self._fallback_model:
                _logger.warning(
                    "rate_limit_429_nim_fallback",
                    error=str(exc),
                    fallback_model=self._fallback_model,
                )
                response = await self._fallback.chat(
                    messages,
                    model=self._fallback_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system,
                    tools=tools,
                    **kwargs,
                )
            else:
                raise

        self._tracker.record(response.input_tokens + response.output_tokens)
        return response
