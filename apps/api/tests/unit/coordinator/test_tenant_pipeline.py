"""Tests for per-tenant config consumption in the search pipeline (Phase 3.2-A Step 4).

Covers:
- _get_adapter_for_tenant: routes to real adapter (aviasales) or synthetic based on slug.
- _build_agents respects affiliate_enabled flag (partner_marker cleared when False).
- auth middleware injects inventory_adapter + affiliate_enabled onto request.state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from travel_agent.api.middleware.auth import TenantAuthMiddleware
from travel_agent.coordinator.streaming import _get_adapter_for_tenant
from travel_agent.providers.aviasales import AviasalesAdapter

# ── _get_adapter_for_tenant ───────────────────────────────────────────────────


class TestGetAdapterForTenant:
    def test_aviasales_slug_returns_none_without_live_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'aviasales' slug must return None when AVIASALES_LIVE is unset."""
        monkeypatch.delenv("AVIASALES_LIVE", raising=False)
        assert _get_adapter_for_tenant("aviasales") is None

    def test_aviasales_slug_returns_adapter_when_live(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'aviasales' slug returns AviasalesAdapter when AVIASALES_LIVE=true and key set."""
        monkeypatch.setenv("AVIASALES_LIVE", "true")
        monkeypatch.setenv("AVIASALES_API_KEY", "test-key")
        result = _get_adapter_for_tenant("aviasales")
        assert isinstance(result, AviasalesAdapter)

    def test_synthetic_slug_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'synthetic' slug falls through to synthetic path even when live flag is set."""
        monkeypatch.setenv("AVIASALES_LIVE", "true")
        monkeypatch.setenv("AVIASALES_API_KEY", "test-key")
        assert _get_adapter_for_tenant("synthetic") is None

    def test_unknown_slug_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unrecognised adapter slug never raises — returns None (synthetic path)."""
        monkeypatch.setenv("AVIASALES_LIVE", "true")
        monkeypatch.setenv("AVIASALES_API_KEY", "test-key")
        assert _get_adapter_for_tenant("amadeus") is None


# ── _build_agents affiliate_enabled ──────────────────────────────────────────


class TestBuildAgentsAffiliate:
    def test_affiliate_enabled_true_uses_partner_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When affiliate_enabled=True, partner_marker equals AVIASALES_PARTNER_ID."""
        monkeypatch.setenv("AVIASALES_PARTNER_ID", "partner123")

        from travel_agent.api.routes.search import _build_agents

        with patch("travel_agent.api.routes.search.get_llm_client_and_model") as mock_get:
            mock_get.return_value = (MagicMock(), "test-model")
            _planner, optimizer = _build_agents("local", affiliate_enabled=True)

        assert optimizer._partner_marker == "partner123"

    def test_affiliate_enabled_false_clears_partner_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When affiliate_enabled=False, partner_marker is empty regardless of env var."""
        monkeypatch.setenv("AVIASALES_PARTNER_ID", "partner123")

        from travel_agent.api.routes.search import _build_agents

        with patch("travel_agent.api.routes.search.get_llm_client_and_model") as mock_get:
            mock_get.return_value = (MagicMock(), "test-model")
            _planner, optimizer = _build_agents("local", affiliate_enabled=False)

        assert optimizer._partner_marker == ""

    def test_affiliate_enabled_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default affiliate_enabled=True is backward compatible."""
        monkeypatch.setenv("AVIASALES_PARTNER_ID", "partner456")

        from travel_agent.api.routes.search import _build_agents

        with patch("travel_agent.api.routes.search.get_llm_client_and_model") as mock_get:
            mock_get.return_value = (MagicMock(), "test-model")
            _planner, optimizer = _build_agents("local")

        assert optimizer._partner_marker == "partner456"


# ── auth middleware injects inventory_adapter + affiliate_enabled ─────────────


class TestAuthMiddlewareTenantConfig:
    """Verify TenantAuthMiddleware sets inventory_adapter and affiliate_enabled."""

    def _make_middleware(self, mode: str = "local") -> TenantAuthMiddleware:
        mw = TenantAuthMiddleware(MagicMock())
        mw._mode = mode
        return mw

    async def test_local_mode_sets_default_adapter_and_affiliate(self) -> None:
        """Local mode must set inventory_adapter='aviasales' and affiliate_enabled=True."""
        mw = self._make_middleware("local")
        request = MagicMock()
        request.url.path = "/search"
        request.state = MagicMock()

        async def _next(_r: object) -> MagicMock:
            return MagicMock()

        await mw.dispatch(request, _next)

        assert request.state.inventory_adapter == "aviasales"
        assert request.state.affiliate_enabled is True

    async def test_synthetic_mode_sets_default_adapter_and_affiliate(self) -> None:
        """Synthetic mode must also set inventory_adapter='aviasales' and affiliate_enabled=True."""
        mw = self._make_middleware("synthetic")
        request = MagicMock()
        request.url.path = "/search"
        request.state = MagicMock()

        async def _next(_r: object) -> MagicMock:
            return MagicMock()

        await mw.dispatch(request, _next)

        assert request.state.inventory_adapter == "aviasales"
        assert request.state.affiliate_enabled is True

    async def test_authenticated_mode_copies_tenant_fields(self) -> None:
        """After successful key resolution, tenant config flows to request.state."""
        mw = self._make_middleware("demo")

        mock_tenant = MagicMock()
        mock_tenant.id = "tenant-uuid-1"
        mock_tenant.inventory_adapter = "amadeus"
        mock_tenant.affiliate_enabled = False

        request = MagicMock()
        request.url.path = "/search"
        request.headers = {"Authorization": "Bearer test-key"}
        request.state = MagicMock()

        async def _next(_r: object) -> MagicMock:
            return MagicMock()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("travel_agent.api.middleware.auth.get_session_factory") as mock_factory,
            patch(
                "travel_agent.api.middleware.auth.resolve_key",
                return_value=mock_tenant,
            ),
        ):
            mock_factory.return_value = MagicMock(return_value=mock_session)
            await mw.dispatch(request, _next)

        assert request.state.inventory_adapter == "amadeus"
        assert request.state.affiliate_enabled is False
