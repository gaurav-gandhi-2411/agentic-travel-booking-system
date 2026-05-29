"""Search cache — in-memory LRU (local) or Redis-backed (production).

When UPSTASH_REDIS_URL is set, a Redis-backed cache is used (via
``travel_agent.cache.redis_cache.RedisSearchCache``).  Otherwise the
in-memory LRU is used, guarded by asyncio.Lock for single-process safety.

Capacity: 50 entries, TTL: 30 minutes.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from collections import OrderedDict
from typing import Protocol

import structlog

from travel_agent.coordinator.state import FlightOption, TravelIntent

_MAX_ENTRIES = 50
_TTL_SECONDS = 1800

_logger = structlog.get_logger(__name__)
_REVISION: str = os.environ.get("K_REVISION") or socket.gethostname()

_CacheEntry = tuple[float, TravelIntent, list[FlightOption]]


class SearchCacheProtocol(Protocol):
    """Structural type shared by in-memory and Redis backends."""

    async def put(
        self, request_id: str, intent: TravelIntent, flights: list[FlightOption]
    ) -> None: ...

    async def get(self, request_id: str) -> tuple[TravelIntent, list[FlightOption]] | None: ...

    async def ping(self) -> bool: ...


class _SearchCache:
    def __init__(self) -> None:
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def put(self, request_id: str, intent: TravelIntent, flights: list[FlightOption]) -> None:
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

    async def ping(self) -> bool:
        return True


def _make_cache() -> SearchCacheProtocol:
    """Factory: return RedisSearchCache if UPSTASH_REDIS_URL is set, else in-memory."""
    url = os.environ.get("UPSTASH_REDIS_URL", "").strip()
    result: SearchCacheProtocol | None = None
    if url:
        try:
            from travel_agent.cache.redis_cache import RedisSearchCache  # noqa: PLC0415

            result = RedisSearchCache(url)
        except Exception as exc:
            _logger.warning(
                "cache_init_fallback",
                error_class=exc.__class__.__name__,
                error_message=str(exc),
                revision=_REVISION,
            )
    backend = "redis" if result is not None else "in_memory"
    _logger.info("cache_backend_selected", backend=backend, revision=_REVISION)
    return result if result is not None else _SearchCache()


search_cache: SearchCacheProtocol = _make_cache()
