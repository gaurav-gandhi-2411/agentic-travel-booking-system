"""Langfuse 4.x observability singleton.

Langfuse 4.x uses an OpenTelemetry-based architecture. The ``Langfuse`` client
is initialised once at module load; callers guard every operation with an
``if lf:`` check so tracing is always optional.

Usage::

    from travel_agent.observability.langfuse_client import (
        get_langfuse,
        get_request_trace,
        set_request_trace,
    )

    lf = get_langfuse()
    if lf:
        span = lf.start_observation(name="search", as_type="span", input={...})
        set_request_trace(span)
        ...
        span.end()
        lf.flush()
"""

from __future__ import annotations

import contextvars
import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Per-request context var — holds the current Langfuse root span/observation
_TRACE_CTX: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "langfuse_request_trace", default=None
)

_client: Any | None = None  # module-level singleton


def get_langfuse() -> Any | None:
    """Return the Langfuse client singleton, or None if keys are not configured.

    Never raises. Tracing is always opt-in.
    """
    global _client  # noqa: PLW0603

    if _client is not None:
        return _client

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()

    if not public_key or not secret_key:
        logger.warning(
            "observability disabled — set LANGFUSE_PUBLIC_KEY to enable",
            provider="langfuse",
        )
        return None

    try:
        from langfuse import Langfuse  # noqa: PLC0415

        host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
    except Exception:
        logger.exception("langfuse client init failed — tracing disabled")
        _client = None

    return _client


def set_request_trace(trace: Any) -> None:
    """Store the current Langfuse root observation in request-scoped context."""
    _TRACE_CTX.set(trace)


def get_request_trace() -> Any | None:
    """Retrieve the current Langfuse root observation, or None if unset."""
    return _TRACE_CTX.get(None)
