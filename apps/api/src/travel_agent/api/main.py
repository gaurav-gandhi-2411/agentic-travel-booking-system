"""FastAPI application entry point.

Phase 0 baseline: single /health endpoint, startup guard, request-ID middleware,
and structlog configuration. Phase 1 expands with routes, DB pool, and OTel wiring.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
import structlog.contextvars
from fastapi import FastAPI

from travel_agent.api.middleware.request_id import RequestIDMiddleware

# ── structlog configuration ───────────────────────────────────────────────────
# Configured once at module load. All loggers emit structured JSON.
# In development, swap JSONRenderer for ConsoleRenderer for human-readable output.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


# ── lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup guards and resource initialisation.

    Fails fast at boot rather than at the first request so that Cloud Run
    readiness probes catch misconfigured environments immediately.
    """
    profile = os.environ.get("LLM_ROUTING_PROFILE", "local")
    if profile in {"eval", "prod"} and not os.environ.get("ANTHROPIC_API_KEY"):
        msg = (
            "LLM_ROUTING_PROFILE=eval|prod requires ANTHROPIC_API_KEY. "
            "Eval is for manual baseline runs only. "
            "Prod expects a tenant-supplied key."
        )
        raise RuntimeError(msg)

    logger.info("startup", llm_routing_profile=profile, phase="0")
    yield
    logger.info("shutdown")


# ── application ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agentic Travel Booking API",
    version="0.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "0"}
