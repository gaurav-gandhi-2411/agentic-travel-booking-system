"""Unit tests for RedisSearchCache graceful failure behaviour."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import structlog.testing

from travel_agent.coordinator.state import (
    CabinClass,
    FlightOption,
    TravelIntent,
    TripType,
    Window,
)


def _intent() -> TravelIntent:
    return TravelIntent(
        origin_iata="DEL",
        destination_iata="DXB",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 30),
        trip_duration_days=5,
        traveler_count=1,
        cabin_class=CabinClass.ECONOMY,
        trip_type=TripType.ROUND_TRIP,
        raw_query="Delhi to Dubai in June",
    )


def _flights() -> list[FlightOption]:
    window = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))
    return [
        FlightOption(
            id="f-001",
            window=window,
            provider="synthetic",
            origin_iata="DEL",
            destination_iata="DXB",
            outbound_departure_at="2026-06-02T08:00:00",
            outbound_arrival_at="2026-06-02T12:00:00",
            airline_code="EK",
            flight_number="EK501",
            cabin_class=CabinClass.ECONOMY,
            price_inr=18000,
            outbound_duration_minutes=240,
            layover_count=0,
        )
    ]


async def test_put_connection_error_does_not_raise() -> None:
    """ConnectionError on Redis set is swallowed; put() returns without raising."""
    from unittest.mock import AsyncMock, patch

    with patch("travel_agent.cache.redis_cache.aioredis.from_url") as mock_from_url:
        mock_client = AsyncMock()
        mock_client.set.side_effect = ConnectionError("Redis unreachable")
        mock_from_url.return_value = mock_client

        from travel_agent.cache.redis_cache import RedisSearchCache

        cache = RedisSearchCache("rediss://fake")
        with structlog.testing.capture_logs() as logs:
            await cache.put("req-fail-001", _intent(), _flights())

    assert len(logs) == 1
    assert logs[0]["event"] == "search_cache_failure"
    assert logs[0]["operation"] == "put"
    assert logs[0]["request_id"] == "req-fail-001"
    assert logs[0]["error_class"] == "ConnectionError"


async def test_get_timeout_returns_none() -> None:
    """asyncio.TimeoutError on Redis get returns None, does not raise."""
    with patch("travel_agent.cache.redis_cache.aioredis.from_url") as mock_from_url:
        mock_client = AsyncMock()
        mock_client.get.side_effect = TimeoutError()
        mock_from_url.return_value = mock_client

        from travel_agent.cache.redis_cache import RedisSearchCache

        cache = RedisSearchCache("rediss://fake")
        with structlog.testing.capture_logs() as logs:
            result = await cache.get("req-fail-002")

    assert result is None
    assert len(logs) == 1
    assert logs[0]["event"] == "search_cache_failure"
    assert logs[0]["operation"] == "get"
    assert logs[0]["error_class"] == "TimeoutError"


async def test_healthy_put_logs_no_warning() -> None:
    """When Redis responds normally, put() completes without any log events."""
    with patch("travel_agent.cache.redis_cache.aioredis.from_url") as mock_from_url:
        mock_client = AsyncMock()
        mock_from_url.return_value = mock_client

        from travel_agent.cache.redis_cache import RedisSearchCache

        cache = RedisSearchCache("rediss://fake")
        with structlog.testing.capture_logs() as logs:
            await cache.put("req-ok-001", _intent(), _flights())

    unexpected = [e for e in logs if e.get("log_level") not in ("info", "debug")]
    assert unexpected == [], f"Expected no warning/error log events on healthy put, got: {unexpected}"
