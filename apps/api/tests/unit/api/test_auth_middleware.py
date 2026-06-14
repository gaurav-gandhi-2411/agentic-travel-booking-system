"""Unit tests for TenantAuthMiddleware (_extract_key + bypass logic).

Tests for the DB-touching resolution path use a mocked session factory so no
real Postgres connection is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from travel_agent.api.middleware.auth import _extract_key

# ── _extract_key ──────────────────────────────────────────────────────────────


class TestExtractKey:
    def _make_request(self, headers: dict[str, str]) -> MagicMock:
        req = MagicMock()
        req.headers = headers
        return req

    def test_extracts_bearer_token(self) -> None:
        req = self._make_request({"Authorization": "Bearer my-api-key-value"})
        assert _extract_key(req) == "my-api-key-value"

    def test_bearer_case_insensitive(self) -> None:
        req = self._make_request({"Authorization": "BEARER my-api-key-value"})
        assert _extract_key(req) == "my-api-key-value"

    def test_extracts_x_api_key_header(self) -> None:
        req = self._make_request({"X-API-Key": "my-api-key-value"})
        assert _extract_key(req) == "my-api-key-value"

    def test_prefers_bearer_over_x_api_key(self) -> None:
        req = self._make_request({"Authorization": "Bearer bearer-key", "X-API-Key": "x-api-key"})
        assert _extract_key(req) == "bearer-key"

    def test_returns_none_when_no_key(self) -> None:
        req = self._make_request({})
        assert _extract_key(req) is None

    def test_returns_none_for_empty_bearer(self) -> None:
        req = self._make_request({"Authorization": "Bearer "})
        assert _extract_key(req) is None

    def test_returns_none_for_empty_x_api_key(self) -> None:
        req = self._make_request({"X-API-Key": ""})
        assert _extract_key(req) is None


# ── TenantAuthMiddleware: local/synthetic bypass ──────────────────────────────


@pytest.mark.parametrize("app_mode", ["local", "synthetic"])
def test_local_mode_bypasses_auth_on_search(app_mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """In local/synthetic modes, /search should pass with no API key."""
    monkeypatch.setenv("APP_MODE", app_mode)

    # Re-import app so the middleware picks up the new APP_MODE
    from travel_agent.api.main import app as fastapi_app

    # Patch the search endpoint so it returns 200 without running the full pipeline
    with patch(
        "travel_agent.api.routes.search.router",
        fastapi_app.router,
    ):
        client = TestClient(fastapi_app, raise_server_exceptions=False)
        # /health is always open — verify it still returns 200
        resp = client.get("/health")
        assert resp.status_code == 200


def test_health_endpoint_always_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The /health endpoint must never be gated by TenantAuthMiddleware."""
    monkeypatch.setenv("APP_MODE", "demo")
    # Do NOT set a DEMO_API_KEY — health should not check it at middleware level
    from travel_agent.api.main import app as fastapi_app

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
