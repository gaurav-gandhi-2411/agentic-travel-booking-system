"""Unit tests for TenantAuthMiddleware (_extract_key + bypass logic).

Tests for the DB-touching resolution path use a mocked session factory so no
real Postgres connection is required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from travel_agent.api.middleware.auth import (
    _AUTH_DB_MAX_ATTEMPTS,
    TenantAuthMiddleware,
    _extract_key,
    _resolve_key_with_retry,
)

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


# ── _resolve_key_with_retry: ADR-0028 pool-exhaustion retry ───────────────────


def _make_factory() -> MagicMock:
    """Stand-in for get_session_factory()'s return value.

    factory() must be callable repeatedly (once per retry attempt), each time
    yielding a fresh async context manager -- resolve_key itself is mocked
    separately per test, so the dummy session's contents don't matter.
    """

    @asynccontextmanager
    async def _session_cm():  # type: ignore[no-untyped-def]
        yield MagicMock()

    return MagicMock(side_effect=_session_cm)


class TestResolveKeyWithRetry:
    async def test_succeeds_first_attempt_no_retry(self) -> None:
        tenant = MagicMock()
        with (
            patch(
                "travel_agent.api.middleware.auth.get_session_factory", return_value=_make_factory()
            ),
            patch(
                "travel_agent.api.middleware.auth.resolve_key", AsyncMock(return_value=tenant)
            ) as mock_resolve,
        ):
            result = await _resolve_key_with_retry("some-key")
        assert result is tenant
        assert mock_resolve.call_count == 1

    async def test_retries_on_operational_error_then_succeeds(self) -> None:
        tenant = MagicMock()
        exc = OperationalError("stmt", {}, Exception("pool full"))
        mock_resolve = AsyncMock(side_effect=[exc, tenant])
        with (
            patch(
                "travel_agent.api.middleware.auth.get_session_factory", return_value=_make_factory()
            ),
            patch("travel_agent.api.middleware.auth.resolve_key", mock_resolve),
            patch("travel_agent.api.middleware.auth.asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            result = await _resolve_key_with_retry("some-key")
        assert result is tenant
        assert mock_resolve.call_count == 2
        mock_sleep.assert_awaited_once()

    async def test_timeout_error_also_retries(self) -> None:
        tenant = MagicMock()
        mock_resolve = AsyncMock(side_effect=[SATimeoutError("timed out"), tenant])
        with (
            patch(
                "travel_agent.api.middleware.auth.get_session_factory", return_value=_make_factory()
            ),
            patch("travel_agent.api.middleware.auth.resolve_key", mock_resolve),
            patch("travel_agent.api.middleware.auth.asyncio.sleep", AsyncMock()),
        ):
            result = await _resolve_key_with_retry("some-key")
        assert result is tenant

    async def test_exhausts_retries_and_raises(self) -> None:
        """After _AUTH_DB_MAX_ATTEMPTS, the last OperationalError propagates so the
        middleware can convert it to a clean 503 -- not swallowed, not infinite."""
        exc = OperationalError("stmt", {}, Exception("pool full"))
        mock_resolve = AsyncMock(side_effect=[exc] * _AUTH_DB_MAX_ATTEMPTS)
        with (
            patch(
                "travel_agent.api.middleware.auth.get_session_factory", return_value=_make_factory()
            ),
            patch("travel_agent.api.middleware.auth.resolve_key", mock_resolve),
            patch("travel_agent.api.middleware.auth.asyncio.sleep", AsyncMock()),
            pytest.raises(OperationalError),
        ):
            await _resolve_key_with_retry("some-key")
        assert mock_resolve.call_count == _AUTH_DB_MAX_ATTEMPTS

    async def test_non_transient_error_is_not_retried(self) -> None:
        """A genuine bug (not a connectivity blip) propagates immediately on the
        first attempt -- retrying it would just fail the same way three times
        instead of once."""
        mock_resolve = AsyncMock(side_effect=ValueError("not a connection problem"))
        with (
            patch(
                "travel_agent.api.middleware.auth.get_session_factory", return_value=_make_factory()
            ),
            patch("travel_agent.api.middleware.auth.resolve_key", mock_resolve),
            pytest.raises(ValueError, match="not a connection problem"),
        ):
            await _resolve_key_with_retry("some-key")
        assert mock_resolve.call_count == 1


class TestAuthMiddlewareBusyResponse:
    """A guarded route returns a clean structured 503 (not a raw exception) when
    the auth DB check exhausts its retries.

    Built as a minimal standalone Starlette app (not the cached
    travel_agent.api.main singleton) because TenantAuthMiddleware reads APP_MODE
    once at construction time; the shared app's middleware stack is built on
    first import and doesn't reconstruct when a later test monkeypatches the
    env var, so re-importing it here would silently inherit whatever mode an
    earlier test in this module left behind.
    """

    def _build_app(self) -> Starlette:
        async def _stub(_request: object) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/search", _stub, methods=["POST"])])
        app.add_middleware(TenantAuthMiddleware)
        return app

    def test_pool_exhaustion_returns_structured_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_MODE", "demo")
        app = self._build_app()

        exc = OperationalError("stmt", {}, Exception("pool full"))
        with patch(
            "travel_agent.api.middleware.auth._resolve_key_with_retry",
            AsyncMock(side_effect=exc),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/search",
                json={"query": "fly from Mumbai to Paris"},
                headers={"X-API-Key": "irrelevant-in-this-test"},
            )
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Service temporarily busy, please retry."}

    def test_healthy_key_resolution_still_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity check on the same minimal app: a normal resolution (no
        exception) still reaches the guarded route, confirming the 503 test
        above is exercising the failure path specifically, not just always
        503-ing regardless of what _resolve_key_with_retry does."""
        monkeypatch.setenv("APP_MODE", "demo")
        app = self._build_app()

        tenant = MagicMock(
            id="11111111-1111-1111-1111-111111111111",
            inventory_adapter="aviasales",
            affiliate_enabled=True,
        )
        with patch(
            "travel_agent.api.middleware.auth._resolve_key_with_retry",
            AsyncMock(return_value=tenant),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/search",
                json={"query": "fly from Mumbai to Paris"},
                headers={"X-API-Key": "a-valid-looking-key"},
            )
        assert resp.status_code == 200
        assert resp.text == "ok"
