"""Tests for AviasalesAdapter using VCR cassettes."""
from __future__ import annotations

from pathlib import Path

import pytest
import vcr

from travel_agent.providers.aviasales import (
    AviasalesAdapter,
    AviasalesClientError,
    AviasalesRateLimitError,
    AviasalesServerError,
)

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
        flights = await adapter.get_flights(
            "BOM", "CDG", "2026-06-01", currency="inr"
        )
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
    with _VCR.use_cassette("flights_429.yaml"), pytest.raises(AviasalesRateLimitError):
        await adapter.get_flights("BOM", "CDG", "2026-06-01")
    await adapter.close()


async def test_get_flights_raises_on_5xx(adapter: AviasalesAdapter) -> None:
    with _VCR.use_cassette("flights_5xx.yaml"), pytest.raises(AviasalesServerError):
        await adapter.get_flights("BOM", "CDG", "2026-06-01")
    await adapter.close()


def test_aviasales_client_error_is_subclass_of_aviasales_error() -> None:
    from travel_agent.providers.aviasales import AviasalesError

    assert issubclass(AviasalesRateLimitError, AviasalesError)
    assert issubclass(AviasalesServerError, AviasalesError)
    assert issubclass(AviasalesClientError, AviasalesError)
