"""Unit tests for tenancy.config — per-tenant config accessors."""

from __future__ import annotations

from unittest.mock import MagicMock

from travel_agent.tenancy.config import (
    get_inventory_adapter,
    get_rate_limit_tier,
    is_affiliate_enabled,
)
from travel_agent.tenancy.models import Tenant


def _make_tenant(
    inventory_adapter: str = "aviasales",
    affiliate_enabled: bool = True,
    rate_limit_tier: str = "standard",
) -> Tenant:
    """Create a lightweight mock Tenant with the given config fields."""
    t = MagicMock(spec=Tenant)
    t.inventory_adapter = inventory_adapter
    t.affiliate_enabled = affiliate_enabled
    t.rate_limit_tier = rate_limit_tier
    return t


class TestGetInventoryAdapter:
    def test_returns_tenant_adapter(self) -> None:
        tenant = _make_tenant(inventory_adapter="aviasales")
        assert get_inventory_adapter(tenant) == "aviasales"

    def test_returns_custom_adapter(self) -> None:
        tenant = _make_tenant(inventory_adapter="amadeus")
        assert get_inventory_adapter(tenant) == "amadeus"


class TestIsAffiliateEnabled:
    def test_returns_true_when_enabled(self) -> None:
        tenant = _make_tenant(affiliate_enabled=True)
        assert is_affiliate_enabled(tenant) is True

    def test_returns_false_when_disabled(self) -> None:
        tenant = _make_tenant(affiliate_enabled=False)
        assert is_affiliate_enabled(tenant) is False


class TestGetRateLimitTier:
    def test_returns_standard_tier(self) -> None:
        tenant = _make_tenant(rate_limit_tier="standard")
        assert get_rate_limit_tier(tenant) == "standard"

    def test_returns_premium_tier(self) -> None:
        tenant = _make_tenant(rate_limit_tier="premium")
        assert get_rate_limit_tier(tenant) == "premium"
