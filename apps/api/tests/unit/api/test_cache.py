"""Unit tests for search cache logging events."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from travel_agent.api.cache import _make_cache
from travel_agent.cache.redis_cache import RedisSearchCache
from travel_agent.coordinator.state import FlightOption, TravelIntent

_INTENT = TravelIntent(
    origin_iata="DEL",
    destination_iata="DXB",
    earliest_departure=date(2026, 12, 1),
    latest_departure=date(2026, 12, 31),
)
_FLIGHTS: list[FlightOption] = []


def _make_redis_cache() -> RedisSearchCache:
    """Construct a RedisSearchCache with a mocked redis client."""
    with patch("redis.asyncio.from_url", return_value=MagicMock()):
        return RedisSearchCache("rediss://fake")


# ---------------------------------------------------------------------------
# _make_cache() log events
# ---------------------------------------------------------------------------


def test_make_cache_logs_backend_selected_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """cache_backend_selected with backend=redis when URL is set and init succeeds."""
    monkeypatch.setenv("UPSTASH_REDIS_URL", "rediss://fake")

    with (
        patch("travel_agent.cache.redis_cache.RedisSearchCache", return_value=MagicMock()),
        structlog.testing.capture_logs() as logs,
    ):
        _make_cache()

    selected = [e for e in logs if e["event"] == "cache_backend_selected"]
    assert len(selected) == 1
    assert selected[0]["backend"] == "redis"
    assert "revision" in selected[0]


def test_make_cache_logs_backend_selected_in_memory_no_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache_backend_selected with backend=in_memory when no URL is configured."""
    monkeypatch.delenv("UPSTASH_REDIS_URL", raising=False)

    with structlog.testing.capture_logs() as logs:
        _make_cache()

    selected = [e for e in logs if e["event"] == "cache_backend_selected"]
    assert len(selected) == 1
    assert selected[0]["backend"] == "in_memory"


def test_make_cache_logs_fallback_on_redis_init_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache_init_fallback is logged when RedisSearchCache.__init__ raises."""
    monkeypatch.setenv("UPSTASH_REDIS_URL", "rediss://fake")

    with (
        patch(
            "travel_agent.cache.redis_cache.RedisSearchCache",
            side_effect=ConnectionError("test error"),
        ),
        structlog.testing.capture_logs() as logs,
    ):
        _make_cache()

    fallback = [e for e in logs if e["event"] == "cache_init_fallback"]
    assert len(fallback) == 1
    assert fallback[0]["error_class"] == "ConnectionError"
    assert fallback[0]["error_message"] == "test error"

    selected = [e for e in logs if e["event"] == "cache_backend_selected"]
    assert len(selected) == 1
    assert selected[0]["backend"] == "in_memory"


# ---------------------------------------------------------------------------
# RedisSearchCache log events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_cache_put_logs_success() -> None:
    """search_cache_put_success is logged on a successful Redis set."""
    cache = _make_redis_cache()
    cache._redis.set = AsyncMock(return_value=True)

    with structlog.testing.capture_logs() as logs:
        await cache.put("req-1", _INTENT, _FLIGHTS)

    success = [e for e in logs if e["event"] == "search_cache_put_success"]
    assert len(success) == 1
    assert success[0]["request_id"] == "req-1"
    assert "revision" in success[0]


@pytest.mark.asyncio
async def test_redis_cache_get_logs_hit() -> None:
    """search_cache_get_result is logged with hit=True when Redis returns data."""
    cache = _make_redis_cache()
    payload = json.dumps({"intent": _INTENT.model_dump(mode="json"), "flights": []})
    cache._redis.get = AsyncMock(return_value=payload)

    with structlog.testing.capture_logs() as logs:
        result = await cache.get("req-1")

    assert result is not None
    hit_logs = [e for e in logs if e["event"] == "search_cache_get_result"]
    assert len(hit_logs) == 1
    assert hit_logs[0]["request_id"] == "req-1"
    assert hit_logs[0]["hit"] is True


@pytest.mark.asyncio
async def test_redis_cache_get_logs_miss() -> None:
    """search_cache_get_result is logged with hit=False when Redis returns None."""
    cache = _make_redis_cache()
    cache._redis.get = AsyncMock(return_value=None)

    with structlog.testing.capture_logs() as logs:
        result = await cache.get("req-1")

    assert result is None
    miss_logs = [e for e in logs if e["event"] == "search_cache_get_result"]
    assert len(miss_logs) == 1
    assert miss_logs[0]["request_id"] == "req-1"
    assert miss_logs[0]["hit"] is False
