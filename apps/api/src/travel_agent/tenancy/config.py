from __future__ import annotations

from travel_agent.tenancy.models import Tenant


def get_inventory_adapter(tenant: Tenant) -> str:
    """Return the inventory adapter slug configured for this tenant."""
    return tenant.inventory_adapter


def is_affiliate_enabled(tenant: Tenant) -> bool:
    """Return True if affiliate deeplinks are enabled for this tenant."""
    return tenant.affiliate_enabled


def get_rate_limit_tier(tenant: Tenant) -> str:
    """Return the rate-limit tier name for this tenant (stored, not enforced)."""
    return tenant.rate_limit_tier
