"""Integration test for RedisSearchCache — requires a live Upstash Redis connection.

Skipped automatically when UPSTASH_REDIS_URL is not set in the environment.
Run manually or in CI with the env var configured.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("UPSTASH_REDIS_URL"),
    reason="UPSTASH_REDIS_URL not set — integration test skipped",
)


@pytest.fixture
def sample_intent():
    from travel_agent.coordinator.state import CabinClass, TravelIntent, TripType

    return TravelIntent(
        origin_iata="DEL",
        destination_iata="BKK",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 30),
        trip_duration_days=7,
        traveler_count=1,
        cabin_class=CabinClass.ECONOMY,
        trip_type=TripType.ROUND_TRIP,
        raw_query="integration test query",
    )


@pytest.fixture
def sample_flights():
    from travel_agent.coordinator.state import CabinClass, FlightOption, Window

    window = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))
    return [
        FlightOption(
            id="integ-f-001",
            window=window,
            provider="synthetic",
            origin_iata="DEL",
            destination_iata="BKK",
            outbound_departure_at="2026-06-02T08:00:00",
            outbound_arrival_at="2026-06-02T14:00:00",
            airline_code="AI",
            flight_number="AI301",
            cabin_class=CabinClass.ECONOMY,
            price_inr=25000,
            outbound_duration_minutes=360,
        )
    ]


async def test_redis_roundtrip(sample_intent, sample_flights):
    """put then get returns the same data (live Redis)."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    cache = RedisSearchCache(os.environ["UPSTASH_REDIS_URL"])
    assert await cache.ping(), "Redis ping failed — check UPSTASH_REDIS_URL"

    await cache.put("test-integration-001", sample_intent, sample_flights)
    result = await cache.get("test-integration-001")
    assert result is not None
    intent, flights = result
    assert intent.origin_iata == sample_intent.origin_iata
    assert len(flights) == len(sample_flights)
    assert flights[0].id == sample_flights[0].id


# ── Failure-mode tests (unreachable host) ─────────────────────────────────────
# These do NOT need a live Upstash URL — they use a local port-1 address that
# is always refused instantly. The pytestmark skip above still applies so they
# only run when UPSTASH_REDIS_URL is set (confirming the full test environment
# is configured), but the actual calls hit the unreachable local address.


async def test_get_unreachable_host_returns_none_not_exception() -> None:
    """get() against a host that refuses connections returns None, not an exception.

    Exercises the try/except in RedisSearchCache.get() with a real (failing)
    network call — something mocks cannot replicate.
    """
    from travel_agent.cache.redis_cache import RedisSearchCache

    bad_cache = RedisSearchCache("redis://127.0.0.1:1")  # port 1: always refused
    result = await bad_cache.get("any-key")
    assert result is None


async def test_put_unreachable_host_does_not_raise(sample_intent, sample_flights) -> None:
    """put() against an unreachable host returns silently instead of raising."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    bad_cache = RedisSearchCache("redis://127.0.0.1:1")
    await bad_cache.put("integration-put-degradation-test", sample_intent, sample_flights)
