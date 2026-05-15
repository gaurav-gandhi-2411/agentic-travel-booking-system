"""Redis-backed search cache using Upstash Redis (standard protocol).

Connects via redis.asyncio using the standard Redis protocol (not the
Upstash HTTP REST client). URL format: rediss://default:<token>@<host>:<port>
(note: rediss:// = TLS, required by Upstash for standard protocol connections).

Max entries enforced via TTL only (unlike the in-memory LRU). With 30-min TTL
and expected request rate, the 50-entry LRU cap is not needed for Redis.
"""

from __future__ import annotations

import json

import redis.asyncio as aioredis

from travel_agent.coordinator.state import FlightOption, TravelIntent

_TTL_SECONDS = 1800  # 30 min
_KEY_PREFIX = "search_cache:"


class RedisSearchCache:
    """Async Redis cache with the same interface as the in-memory _SearchCache."""

    def __init__(self, url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=True)

    async def put(
        self, request_id: str, intent: TravelIntent, flights: list[FlightOption]
    ) -> None:
        key = f"{_KEY_PREFIX}{request_id}"
        payload = json.dumps({
            "intent": intent.model_dump(mode="json"),
            "flights": [f.model_dump(mode="json") for f in flights],
        })
        await self._redis.set(key, payload, ex=_TTL_SECONDS)

    async def get(self, request_id: str) -> tuple[TravelIntent, list[FlightOption]] | None:
        key = f"{_KEY_PREFIX}{request_id}"
        raw: str | None = await self._redis.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        intent = TravelIntent.model_validate(data["intent"])
        flights = [FlightOption.model_validate(f) for f in data["flights"]]
        return intent, flights

    async def ping(self) -> bool:
        """Return True if the Redis connection is healthy."""
        import contextlib  # noqa: PLC0415

        ok = False
        with contextlib.suppress(Exception):
            await self._redis.ping()  # type: ignore[misc]
            ok = True
        return ok
