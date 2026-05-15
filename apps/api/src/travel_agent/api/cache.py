"""In-memory LRU cache for completed search state.

Keyed by request_id (str UUID). Stores (TravelIntent, list[FlightOption]) so
the /refine endpoint can filter and re-optimize without re-running the search.

Capacity: 50 entries, TTL: 30 minutes. No external dependencies.

When UPSTASH_REDIS_URL is set, a Redis-backed cache is used instead of the
in-memory LRU (via ``travel_agent.cache.redis_cache.RedisSearchCache``).
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from typing import Protocol

from travel_agent.coordinator.state import FlightOption, TravelIntent

_MAX_ENTRIES = 50
_TTL_SECONDS = 1800

_CacheEntry = tuple[float, TravelIntent, list[FlightOption]]


class SearchCacheProtocol(Protocol):
    """Structural type for both in-memory and Redis cache backends."""

    async def put(
        self, request_id: str, intent: TravelIntent, flights: list[FlightOption]
    ) -> None: ...

    async def get(self, request_id: str) -> tuple[TravelIntent, list[FlightOption]] | None: ...

    async def ping(self) -> bool: ...


class _SearchCache:
    def __init__(self) -> None:
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()

    async def put(
        self, request_id: str, intent: TravelIntent, flights: list[FlightOption]
    ) -> None:
        if request_id in self._store:
            del self._store[request_id]
        if len(self._store) >= _MAX_ENTRIES:
            self._store.popitem(last=False)
        self._store[request_id] = (time.monotonic(), intent, flights)

    async def get(self, request_id: str) -> tuple[TravelIntent, list[FlightOption]] | None:
        if request_id not in self._store:
            return None
        ts, intent, flights = self._store[request_id]
        if time.monotonic() - ts > _TTL_SECONDS:
            del self._store[request_id]
            return None
        self._store.move_to_end(request_id)
        return intent, flights

    async def ping(self) -> bool:
        """In-memory cache is always available."""
        return True


def _make_cache() -> SearchCacheProtocol:
    """Factory: return RedisSearchCache if UPSTASH_REDIS_URL is set, else in-memory."""
    url = os.environ.get("UPSTASH_REDIS_URL", "").strip()
    if url:
        import contextlib  # noqa: PLC0415

        result: SearchCacheProtocol | None = None
        with contextlib.suppress(Exception):
            from travel_agent.cache.redis_cache import RedisSearchCache  # noqa: PLC0415

            result = RedisSearchCache(url)
        if result is not None:
            return result
    return _SearchCache()


search_cache: SearchCacheProtocol = _make_cache()
