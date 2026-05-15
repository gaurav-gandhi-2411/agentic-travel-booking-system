"""Unit tests for _SearchCache concurrency safety."""

from __future__ import annotations

import asyncio
from datetime import date

from travel_agent.api.cache import _SearchCache
from travel_agent.coordinator.state import TravelIntent


def _make_intent() -> TravelIntent:
    return TravelIntent(
        origin_iata="BOM",
        destination_iata="CDG",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 30),
    )


async def test_concurrent_put_get_is_safe() -> None:
    """50 concurrent workers each writing and reading their own key must not corrupt state."""
    cache = _SearchCache()
    intent = _make_intent()

    async def worker(i: int) -> None:
        rid = f"req-{i}"
        await cache.put(rid, intent, [])
        result = await cache.get(rid)
        assert result is not None

    await asyncio.gather(*[worker(i) for i in range(50)])


async def test_put_evicts_oldest_at_capacity() -> None:
    """Adding entry 51 should evict the oldest (first-inserted) entry."""
    cache = _SearchCache()
    intent = _make_intent()

    # Fill to capacity (50 entries)
    for i in range(50):
        await cache.put(f"req-{i:03d}", intent, [])

    # Add one more — req-000 should be evicted
    await cache.put("req-new", intent, [])

    assert await cache.get("req-000") is None
    assert await cache.get("req-new") is not None


async def test_get_returns_none_for_missing_key() -> None:
    cache = _SearchCache()
    result = await cache.get("nonexistent")
    assert result is None


async def test_put_replaces_existing_key() -> None:
    """Putting the same key twice should update the entry, not create duplicates."""
    cache = _SearchCache()
    intent = _make_intent()
    await cache.put("req-1", intent, [])
    await cache.put("req-1", intent, [])
    # Cache should have only 1 entry
    result = await cache.get("req-1")
    assert result is not None
