"""In-memory LRU cache for completed search state.

Keyed by request_id (str UUID). Stores (TravelIntent, list[FlightOption]) so
the /refine endpoint can filter and re-optimize without re-running the search.

Capacity: 50 entries, TTL: 30 minutes. No external dependencies.
asyncio.Lock guards all mutations for safe concurrent access across uvicorn workers
sharing the same event loop (single-process) or independent processes (multi-worker:
each worker has its own in-process cache; cross-worker cache misses trigger full searches).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from travel_agent.coordinator.state import FlightOption, TravelIntent

_MAX_ENTRIES = 50
_TTL_SECONDS = 1800

_CacheEntry = tuple[float, TravelIntent, list[FlightOption]]


class _SearchCache:
    def __init__(self) -> None:
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def put(
        self, request_id: str, intent: TravelIntent, flights: list[FlightOption]
    ) -> None:
        async with self._lock:
            if request_id in self._store:
                del self._store[request_id]
            if len(self._store) >= _MAX_ENTRIES:
                self._store.popitem(last=False)
            self._store[request_id] = (time.monotonic(), intent, flights)

    async def get(self, request_id: str) -> tuple[TravelIntent, list[FlightOption]] | None:
        async with self._lock:
            if request_id not in self._store:
                return None
            ts, intent, flights = self._store[request_id]
            if time.monotonic() - ts > _TTL_SECONDS:
                del self._store[request_id]
                return None
            self._store.move_to_end(request_id)
            return intent, flights


search_cache = _SearchCache()
