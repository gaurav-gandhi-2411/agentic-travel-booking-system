"""FastAPI application entry point.

Phase 0 baseline: single /health endpoint, startup guard, request-ID middleware.
Phase C (demo): /search SSE endpoint, demo auth middleware, Aviasales startup guard.
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

from travel_agent.api.middleware.auth import DemoAuthMiddleware
from travel_agent.api.middleware.llm_profile import LLMProfileMiddleware
from travel_agent.api.middleware.request_id import RequestIDMiddleware
from travel_agent.api.routes.refine import router as refine_router
from travel_agent.api.routes.search import router as search_router

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

    _DEMO_KEY_SENTINEL = "change-me-before-demo"
    if app_mode == "demo":
        demo_key = os.environ.get("DEMO_API_KEY", "")
        if not demo_key or demo_key == _DEMO_KEY_SENTINEL:
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

    logger.info("startup", llm_routing_profile=profile, app_mode=app_mode, phase="C")
    yield
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
app.add_middleware(DemoAuthMiddleware)
app.add_middleware(RequestIDMiddleware)

app.include_router(search_router)
app.include_router(refine_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "C"}
