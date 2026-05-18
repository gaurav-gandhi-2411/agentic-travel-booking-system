"""Rate throttle for the optimizer eval runner.

Prevents quota exhaustion by tracking usage in a 60-second sliding window and
sleeping before calls that would exceed the limit.

Two pacing modes:
- TPM (tokens per minute): Groq enforces per-minute token quotas.
- RPM (requests per minute): reserved for providers with request-count limits (e.g. NIM).

A provider may appear in TPM_LIMITS, RPM_LIMITS, both, or neither.
- Neither:  no throttle, calls fire freely.
- TPM only: token-based pacing (Groq).
- RPM only: request-count pacing.
- Both:     enforce whichever limit is more restrictive at call time.

On a 429 rate-limit error, retries once with the configured fallback client.
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

# RPM limits per provider. No active entries: NIM free tier uses a finite credit pool
# (not a resettable RPM window) so it is excluded from nightly eval defaults.
# Extend here when a future provider exposes a true per-minute request cap.
RPM_LIMITS: dict[str, int] = {}

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
        # Reached only when estimated_next alone exceeds the limit — no amount of waiting
        # would satisfy the check. Proceed and let the 429 fallback handle it.
        _logger.warning(
            "throttle_estimate_exceeds_limit",
            estimated=estimated_next,
            limit=self._limit,
            current=current,
        )
        return 0.0


class RequestTracker:
    """Sliding-window request counter for RPM-based throttling.

    Records request timestamps and computes how long to wait before the next
    call so the count in the 60-second window stays below rpm_limit.
    """

    def __init__(self, rpm_limit: int, window_s: float = 60.0) -> None:
        self._limit = rpm_limit
        self._window = window_s
        self._events: deque[float] = deque()

    def record(self) -> None:
        """Record a completed request."""
        self._events.append(time.monotonic())

    def _prune(self) -> None:
        cutoff = time.monotonic() - self._window
        while self._events and self._events[0] < cutoff:
            self._events.popleft()

    def wait_seconds(self) -> float:
        """Seconds to sleep before issuing the next request to stay under rpm_limit.

        Returns 0.0 if a call can proceed immediately. When at the limit, waits
        until the oldest in-window request expires, freeing a slot.
        """
        self._prune()
        if len(self._events) < self._limit:
            return 0.0
        oldest_ts = self._events[0]
        wait = (oldest_ts + self._window) - time.monotonic()
        if wait > self._window:
            # Defensive: shouldn't be reachable with a bounded window,
            # but avoids an indefinite sleep if the clock is behaving unexpectedly.
            _logger.warning("rpm_throttle_wait_exceeds_window", wait_s=round(wait, 1))
            return 0.0
        return max(0.0, wait)


class ThrottledLLMClient:
    """LLM client wrapper that enforces TPM and/or RPM rate limits.

    Before each call, computes the required wait from whichever trackers are
    configured (TPM, RPM, both, or neither) and sleeps for the longer of the two.
    On a 429 rate-limit error, retries once with the configured fallback client.
    """

    def __init__(
        self,
        inner: Any,
        tpm_tracker: TokenTracker | None = None,
        *,
        rpm_tracker: RequestTracker | None = None,
        fallback: Any | None = None,
        fallback_model: str = "",
    ) -> None:
        self._inner = inner
        self._tpm_tracker = tpm_tracker
        self._rpm_tracker = rpm_tracker
        self._fallback = fallback
        self._fallback_model = fallback_model

    async def chat(  # noqa: PLR0913
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
        tpm_wait = (
            self._tpm_tracker.wait_seconds(_CALL_TOKEN_ESTIMATE) if self._tpm_tracker else 0.0
        )
        rpm_wait = self._rpm_tracker.wait_seconds() if self._rpm_tracker else 0.0
        wait = max(tpm_wait, rpm_wait)
        if wait > 0:
            _logger.info(
                "rate_limit_throttle",
                wait_s=round(wait, 1),
                tpm_wait_s=round(tpm_wait, 1),
                rpm_wait_s=round(rpm_wait, 1),
            )
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
                    "rate_limit_429_fallback",
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

        if self._tpm_tracker:
            self._tpm_tracker.record(response.input_tokens + response.output_tokens)
        if self._rpm_tracker:
            self._rpm_tracker.record()
        return response
