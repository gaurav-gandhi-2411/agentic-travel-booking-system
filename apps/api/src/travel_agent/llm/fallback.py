"""Fallback-capable LLM client — tries each hop in a configured chain in order.

See docs/architecture/adr/0027-llm-fallback-chain.md and spec.md. Hop 0 is always
the routing profile's primary provider/model, supplied per-call via chat()'s
``model`` kwarg exactly like a bare adapter — callers (PlannerAgent, OptimizerAgent)
need no changes. Hops 1..N come from the profile's ``fallback_chain`` config
(llm_routing.yaml) and each carries its own fixed model.

Falls back only on ``LLMError.retryable`` errors (429, timeout, 5xx). A
non-retryable error (400, malformed request) surfaces immediately and is never
retried on a later hop, since the same bad request would fail identically
everywhere — see spec.md "Hard rules".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sentry_sdk
import structlog

from travel_agent.llm.base import LLMClient, LLMError, LLMResponse, Message, ToolDefinition

_logger = structlog.get_logger(__name__)

# Sentry scope tag set around every hop attempt (ADR-0028 fix (b)). Sentry's
# OpenAIIntegration auto-captures ANY exception a chat.completions.create() call
# raises -- including ones this class goes on to recover from via the next hop --
# which would otherwise generate a spurious "RateLimitError" Sentry issue for
# every successfully-handled retryable failure. observability/sentry.py's
# before_send drops auto-captured events carrying this tag when the exception is
# one of the known-retryable provider classes, since those cases are already
# reported with richer context by this module's own capture_message (on a served
# fallback) / capture_exception (on full exhaustion) calls below.
LLM_FALLBACK_MANAGED_TAG = "llm_fallback_managed"


@dataclass(frozen=True)
class FallbackHop:
    """One provider/model/client triple in a fallback chain."""

    provider: str
    model: str
    client: LLMClient


class AllProvidersExhaustedError(LLMError):
    """Every hop in the fallback chain failed with a retryable error."""


class FallbackLLMClient:
    """LLMClient that retries a chat() call against successive hops on retryable errors.

    Each attempt is logged via structlog (``llm_fallback_attempt_failed`` /
    ``llm_fallback_served``). A successful fallback (any hop after the primary)
    also reaches Sentry as a warning-level message; full-chain exhaustion reaches
    Sentry as a captured exception — fallbacks are observable, never silent.
    """

    def __init__(
        self,
        primary_client: LLMClient,
        primary_provider: str,
        fallbacks: list[FallbackHop],
    ) -> None:
        if not fallbacks:
            msg = "FallbackLLMClient requires at least one fallback hop"
            raise ValueError(msg)
        self._primary_client = primary_client
        self._primary_provider = primary_provider
        self._fallbacks = fallbacks

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
        hops: list[tuple[str, str, LLMClient]] = [
            (self._primary_provider, model, self._primary_client),
            *((h.provider, h.model, h.client) for h in self._fallbacks),
        ]
        last_exc: LLMError | None = None

        for i, (provider, hop_model, client) in enumerate(hops):
            is_last = i == len(hops) - 1
            try:
                with sentry_sdk.new_scope() as scope:
                    scope.set_tag(LLM_FALLBACK_MANAGED_TAG, "true")
                    response = await client.chat(
                        messages,
                        model=hop_model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        tools=tools,
                        **kwargs,
                    )
            except LLMError as exc:
                _logger.warning(
                    "llm_fallback_attempt_failed",
                    hop_index=i,
                    provider=provider,
                    model=hop_model,
                    retryable=exc.retryable,
                    error=str(exc),
                )
                if not exc.retryable:
                    raise  # e.g. 400 — never falls back, surfaces the real error
                last_exc = exc
                if is_last:
                    sentry_sdk.capture_exception(exc)
                    msg = (
                        f"All {len(hops)} LLM providers exhausted "
                        f"(last: {provider}/{hop_model}): {exc}"
                    )
                    raise AllProvidersExhaustedError(msg, retryable=False) from exc
                continue
            else:
                if i > 0:
                    _logger.info(
                        "llm_fallback_served",
                        hop_index=i,
                        provider=provider,
                        model=hop_model,
                        from_provider=hops[0][0],
                        from_model=hops[0][1],
                        reason=str(last_exc),
                    )
                    sentry_sdk.capture_message(
                        f"LLM fallback served: {hops[0][0]}/{hops[0][1]} -> "
                        f"{provider}/{hop_model} (reason: {last_exc})",
                        level="warning",
                    )
                return response

        # Unreachable: __init__ guarantees >=1 fallback, so hops has >=2 entries,
        # and every iteration above either returns or raises.
        unreachable_msg = "unreachable"
        raise AssertionError(unreachable_msg)  # pragma: no cover
