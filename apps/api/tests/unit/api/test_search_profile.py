"""Unit tests for routes/search.py's _resolve_profile / _ALLOWED_PROFILES.

Regression coverage for the profile-selection bug (2026-08-22): ProfileToggle.tsx's
default profile, demo-gpt-oss-120b, was missing from this route's allowlist, so it
silently fell through to the env-default profile regardless of what the user selected.
"""

from __future__ import annotations

import pytest

from travel_agent.api.routes.search import _ALLOWED_PROFILES, _resolve_profile


def test_resolve_profile_llama_returns_as_is() -> None:
    assert _resolve_profile("demo-llama") == "demo-llama"


def test_resolve_profile_gpt_oss_120b_returns_as_is() -> None:
    """Regression: the frontend's default profile must resolve as-is here."""
    assert _resolve_profile("demo-gpt-oss-120b") == "demo-gpt-oss-120b"


def test_resolve_profile_unknown_demo_env_returns_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "demo")
    assert _resolve_profile("not-a-profile") == "demo-haiku"


def test_resolve_profile_unknown_non_demo_env_returns_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "prod")
    assert _resolve_profile(None) == "prod"


def test_allowed_profiles_contains_frontend_default() -> None:
    """ProfileToggle.tsx's DEFAULT_PROFILE and its only other option must both be allowed."""
    assert "demo-gpt-oss-120b" in _ALLOWED_PROFILES
    assert "demo-llama" in _ALLOWED_PROFILES
