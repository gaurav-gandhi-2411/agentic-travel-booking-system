"""FastAPI application entry point.

Phase 0 baseline: single /health endpoint, startup guard, request-ID middleware.
Phase C (demo): /search SSE endpoint, demo auth middleware, Aviasales startup guard.
Phase 2B: Langfuse observability bootstrap, Redis cache health, cost telemetry.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
import structlog.contextvars
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from travel_agent.api.cache import search_cache
from travel_agent.api.middleware.auth import TenantAuthMiddleware
from travel_agent.api.middleware.llm_profile import LLMProfileMiddleware
from travel_agent.api.middleware.request_id import RequestIDMiddleware
from travel_agent.api.routes.book import router as book_router
from travel_agent.api.routes.refine import router as refine_router
from travel_agent.api.routes.search import router as search_router
from travel_agent.observability.langfuse_client import get_langfuse
from travel_agent.observability.sentry import init_sentry

load_dotenv()

# ── structlog configuration ───────────────────────────────────────────────────
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
    init_sentry()
    profile = os.environ.get("LLM_ROUTING_PROFILE", "local")
    app_mode = os.environ.get("APP_MODE", "synthetic")

    anthropic_profiles = {"eval", "prod", "demo", "demo-haiku"}
    if profile in anthropic_profiles and not os.environ.get("ANTHROPIC_API_KEY"):
        msg = (
            f"LLM_ROUTING_PROFILE={profile} requires ANTHROPIC_API_KEY. "
            "Eval is for manual baseline runs only. "
            "Prod expects a tenant-supplied key."
        )
        raise RuntimeError(msg)

    if app_mode == "demo" and not os.environ.get("AVIASALES_API_KEY"):
        msg = (
            "APP_MODE=demo requires AVIASALES_API_KEY. "
            "Set the env var or switch APP_MODE=synthetic to use synthetic data."
        )
        raise RuntimeError(msg)

    _demo_key_sentinel = "change-me-before-demo"
    if app_mode == "demo":
        demo_key = os.environ.get("DEMO_API_KEY", "")
        if not demo_key or demo_key == _demo_key_sentinel:
            msg = (
                "APP_MODE=demo requires DEMO_API_KEY to be set to a non-default value. "
                "Set the env var to a secret string. "
                "The default 'change-me-before-demo' is not accepted."
            )
            raise RuntimeError(msg)

    if app_mode == "demo" and not os.environ.get("GROQ_API_KEY"):
        logger.warning(
            "GROQ_API_KEY not set — X-LLM-Profile: demo-llama requests will fail at runtime."
        )

    if app_mode == "demo" and not os.environ.get("OPENROUTER_API_KEY"):
        logger.warning(
            "OPENROUTER_API_KEY not set — X-LLM-Profile: demo-qwen requests will fail at runtime."
        )

    # Runtime-role guard: whenever a database is configured, the connected role MUST be a
    # least-privilege, non-superuser, non-BYPASSRLS role. A bypass role (e.g. the managed
    # platform 'postgres' admin role on Supabase) would silently void FORCE-RLS tenant
    # isolation. Refuse to start otherwise — structurally enforce "never serve as postgres".
    if os.environ.get("DATABASE_URL"):
        from travel_agent.persistence.engine import (  # noqa: PLC0415
            assert_runtime_role_unprivileged,
            get_session_factory,
        )

        _factory = get_session_factory()
        async with _factory() as _session:
            await assert_runtime_role_unprivileged(_session)
        logger.info("runtime_db_role_verified")

    # Seed the demo tenant if DATABASE_URL is configured (APP_MODE=demo only).
    # seed_demo_tenant is idempotent: insert-then-catch IntegrityError, safe on
    # every restart. Skipped in synthetic/local modes (no DATABASE_URL needed).
    if app_mode == "demo" and os.environ.get("DATABASE_URL"):
        from travel_agent.persistence.engine import get_session_factory  # noqa: PLC0415
        from travel_agent.tenancy.service import seed_demo_tenant  # noqa: PLC0415

        _factory = get_session_factory()
        async with _factory() as _session:
            await seed_demo_tenant(_session)
        logger.info("demo_tenant_seeded")

    # Langfuse observability — optional; never raises on missing keys
    lf = get_langfuse()
    if lf is not None:
        logger.info("observability enabled", provider="langfuse")
    else:
        logger.warning("observability disabled — set LANGFUSE_PUBLIC_KEY to enable")

    logger.info("startup", llm_routing_profile=profile, app_mode=app_mode, phase="C")
    yield
    # Flush Langfuse on shutdown so buffered events are not lost
    if lf is not None:
        import contextlib  # noqa: PLC0415

        with contextlib.suppress(Exception):
            lf.flush()
    logger.info("shutdown")


# ── application ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agentic Travel Booking API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://agentic-travel-booking-system.vercel.app",
        "http://localhost:3000",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-LLM-Profile"],
)
app.add_middleware(LLMProfileMiddleware)
app.add_middleware(TenantAuthMiddleware)
app.add_middleware(RequestIDMiddleware)

app.include_router(search_router)
app.include_router(refine_router)
app.include_router(book_router)


@app.get("/health", response_model=None)
async def health() -> JSONResponse:
    app_mode = os.environ.get("APP_MODE", "synthetic")
    cache_ok = await search_cache.ping()
    if not cache_ok and app_mode == "prod":
        return JSONResponse(
            {"status": "degraded", "phase": "C", "cache": "unreachable"},
            status_code=503,
        )
    if not cache_ok:
        logger.warning("cache_ping_failed", mode=app_mode)
    payload: dict[str, object] = {
        "status": "ok",
        "phase": "C",
        "cache": "ok" if cache_ok else "degraded",
    }
    return JSONResponse(payload)
