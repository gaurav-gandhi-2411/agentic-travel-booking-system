"""Unit tests for LLMProfileMiddleware / ALLOWED_PROFILES.

Regression coverage for the profile-selection bug (2026-08-22): this middleware's
ALLOWED_PROFILES gates request.state.llm_profile before either route's own
_resolve_profile ever runs -- a value missing here is silently nulled out at the
edge regardless of what the route-level allowlist accepts. ProfileToggle.tsx's
default profile (demo-gpt-oss-120b) was missing here, so every /search and
/refine request fell through to the LLM_ROUTING_PROFILE env default.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from travel_agent.api.middleware.llm_profile import ALLOWED_PROFILES, LLMProfileMiddleware


def _echo_profile(request: Request) -> JSONResponse:
    return JSONResponse({"llm_profile": request.state.llm_profile})


def _make_app() -> Starlette:
    app = Starlette(routes=[Route("/echo", _echo_profile)])
    app.add_middleware(LLMProfileMiddleware)
    return app


def test_allowed_profiles_contains_frontend_default() -> None:
    """ProfileToggle.tsx's DEFAULT_PROFILE and its only other option must both be allowed."""
    assert "demo-gpt-oss-120b" in ALLOWED_PROFILES
    assert "demo-llama" in ALLOWED_PROFILES


def test_dispatch_accepts_gpt_oss_120b_header() -> None:
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"X-LLM-Profile": "demo-gpt-oss-120b"})
    assert resp.json() == {"llm_profile": "demo-gpt-oss-120b"}


def test_dispatch_accepts_llama_header() -> None:
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"X-LLM-Profile": "demo-llama"})
    assert resp.json() == {"llm_profile": "demo-llama"}


def test_dispatch_rejects_unknown_header() -> None:
    client = TestClient(_make_app())
    resp = client.get("/echo", headers={"X-LLM-Profile": "not-a-profile"})
    assert resp.json() == {"llm_profile": None}


def test_dispatch_defaults_to_none_when_absent() -> None:
    client = TestClient(_make_app())
    resp = client.get("/echo")
    assert resp.json() == {"llm_profile": None}
