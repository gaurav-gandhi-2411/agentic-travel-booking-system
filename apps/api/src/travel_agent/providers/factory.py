"""Provider factory — maps inventory adapter slugs to BookableInventoryProvider instances.

Uses a module-level registry so the MockBookableProvider singleton persists across
requests, preserving idempotency state for the lifetime of the server process.

Tests that exercise stream_book / stream_cancel directly should create their own
MockBookableProvider instances (not use this factory) so test state is isolated.
"""

from __future__ import annotations

from travel_agent.providers.base import BookableInventoryProvider
from travel_agent.providers.mock_bookable.provider import MockBookableProvider

# Keyed by slug. Populated lazily on first request.
_PROVIDERS: dict[str, BookableInventoryProvider] = {}


def get_bookable_provider(slug: str) -> BookableInventoryProvider | None:
    """Return the BookableInventoryProvider for *slug*, or None if the slug is search-only.

    Currently only "mock_bookable" is bookable. "aviasales" and all other slugs
    return None (search-only tenants).
    """
    if slug == "mock_bookable":
        if slug not in _PROVIDERS:
            _PROVIDERS[slug] = MockBookableProvider()
        return _PROVIDERS[slug]
    return None
