"""Tests for AviasalesAdapter using VCR cassettes and mock retries."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import vcr

from travel_agent.providers.aviasales import (
    AviasalesAdapter,
    AviasalesClientError,
    AviasalesRateLimitError,
    AviasalesServerError,
)
from travel_agent.providers.aviasales.adapter import _call_with_retry

_CASSETTE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "cassettes" / "aviasales"

_VCR = vcr.VCR(
    cassette_library_dir=str(_CASSETTE_DIR),
    record_mode="none",
    filter_headers=["x-access-token", "authorization", "cookie"],
    decode_compressed_response=True,
    match_on=["method", "scheme", "host", "port", "path"],
)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> AviasalesAdapter:
    monkeypatch.setenv("AVIASALES_API_KEY", "test-key-123")
    return AviasalesAdapter()


def test_adapter_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AVIASALES_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AVIASALES_API_KEY"):
        AviasalesAdapter()


async def test_get_flights_happy_path(adapter: AviasalesAdapter) -> None:
    with _VCR.use_cassette("flights_happy_path.yaml"):
        flights = await adapter.get_flights("BOM", "CDG", "2026-06-01", currency="inr")
    assert len(flights) == 2
    assert flights[0]["price"] == 47500
    assert flights[0]["airline"] == "6E"
    assert flights[1]["price"] == 91500
    assert flights[1]["airline"] == "AI"
    await adapter.close()


async def test_get_flights_sorted_by_price(adapter: AviasalesAdapter) -> None:
    with _VCR.use_cassette("flights_happy_path.yaml"):
        flights = await adapter.get_flights("BOM", "CDG", "2026-06-01")
    prices = [f["price"] for f in flights]
    assert prices == sorted(prices)
    await adapter.close()


async def test_get_flights_raises_on_429(adapter: AviasalesAdapter) -> None:
    # Patch sleep so retries don't slow down CI (cassette has 3 identical 429 responses)
    with (
        _VCR.use_cassette("flights_429.yaml"),
        patch("travel_agent.providers.aviasales.adapter.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(AviasalesRateLimitError),
    ):
        await adapter.get_flights("BOM", "CDG", "2026-06-01")
    await adapter.close()


async def test_get_flights_raises_on_5xx(adapter: AviasalesAdapter) -> None:
    # Patch sleep so retries don't slow down CI (cassette has 3 identical 5xx responses)
    with (
        _VCR.use_cassette("flights_5xx.yaml"),
        patch("travel_agent.providers.aviasales.adapter.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(AviasalesServerError),
    ):
        await adapter.get_flights("BOM", "CDG", "2026-06-01")
    await adapter.close()


def test_aviasales_client_error_is_subclass_of_aviasales_error() -> None:
    from travel_agent.providers.aviasales import AviasalesError

    assert issubclass(AviasalesRateLimitError, AviasalesError)
    assert issubclass(AviasalesServerError, AviasalesError)
    assert issubclass(AviasalesClientError, AviasalesError)


# ── retry / backoff tests ─────────────────────────────────────────────────────


def _make_response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    """Build a minimal httpx.Response for mocking."""
    body = json_body or {}
    import json

    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )


async def test_retry_429_then_success() -> None:
    """429 → 429 → 200 should succeed after two retries."""
    success_response = _make_response(200, {"data": [{"price": 47500}]})
    rate_limit_response = _make_response(429)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(
        side_effect=[rate_limit_response, rate_limit_response, success_response]
    )

    with patch("travel_agent.providers.aviasales.adapter.asyncio.sleep", new_callable=AsyncMock):
        result = await _call_with_retry(mock_client, "/test", {})

    assert result == {"data": [{"price": 47500}]}
    assert mock_client.get.call_count == 3


async def test_retry_three_times_429_raises() -> None:
    """Three consecutive 429s should raise AviasalesRateLimitError."""
    rate_limit_response = _make_response(429)
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=rate_limit_response)

    with (
        patch("travel_agent.providers.aviasales.adapter.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(AviasalesRateLimitError),
    ):
        await _call_with_retry(mock_client, "/test", {})

    assert mock_client.get.call_count == 3


async def test_non_retryable_400_raises_immediately() -> None:
    """A 400 Bad Request must raise AviasalesClientError on the first attempt."""
    bad_response = _make_response(400)
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=bad_response)

    with pytest.raises(AviasalesClientError):
        await _call_with_retry(mock_client, "/test", {})

    # Should NOT retry — exactly 1 call
    assert mock_client.get.call_count == 1
