from __future__ import annotations

import os
import urllib.parse
from typing import Any

from travel_agent.llm.fallback import LLM_FALLBACK_MANAGED_TAG

# Headers that can carry tenant credentials or bearer tokens.
_SCRUB_HEADERS: frozenset[str] = frozenset({"x-api-key", "authorization"})

# Query-param names used by our upstream APIs (Aviasales/Travelpayouts uses
# "token=<key>" in GET URLs — this would surface in HTTP breadcrumbs).
_SCRUB_QUERY_PARAMS: frozenset[str] = frozenset({"token", "api_key", "apikey", "key"})

# ADR-0028 fix (b) — provider exception classes FallbackLLMClient treats as
# retryable. Sentry's OpenAIIntegration auto-captures these unconditionally,
# before FallbackLLMClient even gets a chance to fall back to the next hop, so a
# 429 that's fully recovered a moment later would otherwise still generate a
# spurious "RateLimitError" Sentry issue. Dropped ONLY when tagged
# llm_fallback_managed (i.e. raised from inside FallbackLLMClient.chat()) --
# calls outside a fallback chain (other routing profiles) are untouched, and a
# non-retryable exception (e.g. a genuine bad API key) still reaches Sentry even
# inside a fallback-managed call, since nothing else captures that case.
_RETRYABLE_PROVIDER_EXCEPTION_TYPES: frozenset[str] = frozenset(
    {"RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"}
)


def _is_recovered_fallback_hop_noise(event: dict[str, Any]) -> bool:
    """True if this event is an auto-captured, already-handled fallback-hop failure.

    Tags may arrive as a plain dict or as a list of [key, value] pairs depending
    on SDK processing stage by the time before_send runs -- handle both shapes.
    """
    tags = event.get("tags")
    tag_pairs: list[tuple[Any, Any]] = []
    if isinstance(tags, dict):
        tag_pairs = list(tags.items())
    elif isinstance(tags, list):
        tag_pairs = [tuple(pair) for pair in tags if isinstance(pair, (list, tuple))]
    if (LLM_FALLBACK_MANAGED_TAG, "true") not in tag_pairs:
        return False

    exc_values = (event.get("exception") or {}).get("values") or []
    return any(
        isinstance(v, dict) and v.get("type") in _RETRYABLE_PROVIDER_EXCEPTION_TYPES
        for v in exc_values
    )


def _scrub_url(raw: object) -> object:
    """Redact sensitive query params from a URL string; return raw if not parseable."""
    if not isinstance(raw, str) or "?" not in raw:
        return raw
    try:
        parsed = urllib.parse.urlparse(raw)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {
            k: (["[Scrubbed]"] if k.lower() in _SCRUB_QUERY_PARAMS else v)
            for k, v in params.items()
        }
        return parsed._replace(query=urllib.parse.urlencode(cleaned, doseq=True)).geturl()
    except Exception:
        return raw


def _before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Scrub credential-bearing data before the event leaves the process, and drop
    already-recovered LLM fallback-hop noise (ADR-0028 fix (b)).

    Covers three scrub surfaces:
    1. Request headers: X-API-Key, Authorization (case-insensitive key match)
    2. Inbound request URL query params (defensive; our API doesn't use key-in-URL
       but scrubbing here catches any accidental logging of upstream redirect URLs)
    3. HTTP breadcrumb URLs: outbound calls to Aviasales/Groq/Anthropic captured
       by the SDK's httpx/requests instrumentation; Travelpayouts uses token=<key>
       as a query param so breadcrumb URLs are the highest-risk surface

    Plus one drop rule: an auto-captured retryable provider exception that
    FallbackLLMClient already recovered from (or will separately report with
    richer context on full exhaustion) never leaves the process at all.
    """
    if _is_recovered_fallback_hop_noise(event):
        return None

    request = event.get("request")
    if isinstance(request, dict):
        # 1. Headers
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if isinstance(key, str) and key.lower() in _SCRUB_HEADERS:
                    headers[key] = "[Scrubbed]"
        # 2. Inbound request URL
        if "url" in request:
            request["url"] = _scrub_url(request["url"])

    # 3. HTTP breadcrumb URLs (outbound calls captured by SDK instrumentation)
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        values = breadcrumbs.get("values")
        if isinstance(values, list):
            for crumb in values:
                if isinstance(crumb, dict):
                    data = crumb.get("data")
                    if isinstance(data, dict) and "url" in data:
                        data["url"] = _scrub_url(data["url"])

    return event


def init_sentry() -> None:
    """Initialise Sentry SDK if SENTRY_DSN is set; no-op otherwise.

    Wires the FastAPI integration for automatic exception capture.
    DSN is injected via environment variable — never hardcoded.

    The lazy import of sentry_sdk is intentional: the SDK has a non-trivial
    startup cost (thread spawning, monkey-patching) and we only pay it when
    a DSN is actually configured.

    Free Developer plan quota (5,000 errors/month):
    - traces_sample_rate=0.1: 10% of transactions sampled (errors always sent)
    - profiles_sample_rate=0.0: profiling disabled entirely
    - send_default_pii=False: no IPs, user agents, or cookie data attached
    - before_send scrubs X-API-Key, Authorization headers AND token= query params
      in both the inbound request URL and outbound HTTP breadcrumb URLs
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    import sentry_sdk  # noqa: PLC0415
    from sentry_sdk.integrations.fastapi import FastApiIntegration  # noqa: PLC0415
    from sentry_sdk.integrations.starlette import StarletteIntegration  # noqa: PLC0415

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("APP_ENV", "development"),
        release=os.environ.get("K_REVISION"),  # Cloud Run injects the revision slug
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        profiles_sample_rate=0.0,
        send_default_pii=False,
        before_send=_before_send,  # type: ignore[arg-type]
    )
