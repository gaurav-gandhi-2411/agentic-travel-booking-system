"""Unit tests for RedisSearchCache using a mock redis client."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from travel_agent.coordinator.state import (
    CabinClass,
    FlightOption,
    TravelIntent,
    TripType,
    Window,
)


@pytest.fixture
def sample_intent() -> TravelIntent:
    return TravelIntent(
        origin_iata="DEL",
        destination_iata="BKK",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 30),
        trip_duration_days=7,
        traveler_count=1,
        cabin_class=CabinClass.ECONOMY,
        trip_type=TripType.ROUND_TRIP,
        raw_query="fly from Delhi to Bangkok in June",
    )


@pytest.fixture
def sample_flights() -> list[FlightOption]:
    window = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))
    return [
        FlightOption(
            id="f-001",
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
            layover_count=0,
        )
    ]


@pytest.fixture
def mock_redis():
    with patch("travel_agent.cache.redis_cache.aioredis.from_url") as mock_from_url:
        mock_client = AsyncMock()
        mock_from_url.return_value = mock_client
        yield mock_client


async def test_put_serializes_and_sets_ttl(
    mock_redis: AsyncMock,
    sample_intent: TravelIntent,
    sample_flights: list[FlightOption],
) -> None:
    """put() serialises to JSON and stores with 30-min TTL."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    cache = RedisSearchCache("rediss://fake")
    await cache.put("req-1", sample_intent, sample_flights)

    mock_redis.set.assert_called_once()
    call_args = mock_redis.set.call_args
    assert call_args[0][0] == "search_cache:req-1"
    assert call_args[1]["ex"] == 1800
    payload = json.loads(call_args[0][1])
    assert "intent" in payload
    assert "flights" in payload


async def test_get_returns_none_on_miss(
    mock_redis: AsyncMock,
    sample_intent: TravelIntent,
    sample_flights: list[FlightOption],
) -> None:
    """get() returns None when the key is absent."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    mock_redis.get.return_value = None
    cache = RedisSearchCache("rediss://fake")
    result = await cache.get("missing-id")
    assert result is None


async def test_get_deserializes_correctly(
    mock_redis: AsyncMock,
    sample_intent: TravelIntent,
    sample_flights: list[FlightOption],
) -> None:
    """get() returns a correctly deserialised (intent, flights) tuple."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    payload = json.dumps({
        "intent": sample_intent.model_dump(mode="json"),
        "flights": [f.model_dump(mode="json") for f in sample_flights],
    })
    mock_redis.get.return_value = payload

    cache = RedisSearchCache("rediss://fake")
    result = await cache.get("req-1")
    assert result is not None
    intent, flights = result
    assert intent.origin_iata == "DEL"
    assert len(flights) == 1
    assert flights[0].id == "f-001"


async def test_ping_returns_true_on_success(mock_redis: AsyncMock) -> None:
    """ping() returns True when Redis responds."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    mock_redis.ping.return_value = True
    cache = RedisSearchCache("rediss://fake")
    assert await cache.ping() is True


async def test_ping_returns_false_on_error(mock_redis: AsyncMock) -> None:
    """ping() returns False when the connection fails."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    mock_redis.ping.side_effect = Exception("connection refused")
    cache = RedisSearchCache("rediss://fake")
    assert await cache.ping() is False


async def test_put_uses_correct_key_prefix(
    mock_redis: AsyncMock,
    sample_intent: TravelIntent,
    sample_flights: list[FlightOption],
) -> None:
    """put() uses the search_cache: prefix for keys."""
    from travel_agent.cache.redis_cache import RedisSearchCache

    cache = RedisSearchCache("rediss://fake")
    await cache.put("my-request-id", sample_intent, sample_flights)
    key_used = mock_redis.set.call_args[0][0]
    assert key_used == "search_cache:my-request-id"
