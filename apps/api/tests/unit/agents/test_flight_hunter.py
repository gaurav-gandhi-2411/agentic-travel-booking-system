"""Unit tests for FlightHunterAgent."""
from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from travel_agent.agents.flight_hunter import FlightHunterAgent, _map_raw_to_flight_option
from travel_agent.coordinator.state import (
    CabinClass,
    RequestState,
    TravelIntent,
    TripType,
    Window,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

_WINDOW = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))

_RAW_FLIGHT: dict[str, Any] = {
    "origin": "BOM",
    "destination": "CDG",
    "origin_airport": "BOM",
    "destination_airport": "CDG",
    "price": 47500,
    "airline": "6E",
    "flight_number": 1452,
    "departure_at": "2026-06-01T02:30:00+05:30",
    "return_at": "2026-06-08T10:00:00+05:30",
    "transfers": 2,
    "return_transfers": 2,
    "duration": 1020,
    "duration_to": 510,
    "duration_back": 510,
    "link": "/search/BOM0106CDG08062026",
}


def _make_intent(
    origin: str = "BOM",
    destination: str = "CDG",
    cabin: CabinClass = CabinClass.ECONOMY,
    earliest: date = date(2026, 6, 1),
    latest: date = date(2026, 6, 8),
) -> TravelIntent:
    return TravelIntent(
        origin_iata=origin,
        destination_iata=destination,
        earliest_departure=earliest,
        latest_departure=latest,
        cabin_class=cabin,
        trip_type=TripType.ROUND_TRIP,
        raw_query="fly from Mumbai to Paris next month",
    )


def _make_state(
    intent: TravelIntent | None = None,
    windows: list[Window] | None = None,
    budget_overrides: dict[str, int] | None = None,
) -> RequestState:
    state = RequestState()
    state.intent = intent or _make_intent()
    state.candidate_windows = windows if windows is not None else [_WINDOW]
    if budget_overrides:
        for k, v in budget_overrides.items():
            setattr(state.call_budget, k, v)
    return state


def _mock_adapter(raw_flights: list[dict[str, Any]]) -> Any:
    adapter = MagicMock()
    adapter.get_flights = AsyncMock(return_value=raw_flights)
    return adapter


# ── mapping tests ─────────────────────────────────────────────────────────────


def test_map_raw_sets_provider_aviasales() -> None:
    opt = _map_raw_to_flight_option(_RAW_FLIGHT, _WINDOW, CabinClass.ECONOMY)
    assert opt.provider == "aviasales"


def test_map_raw_copies_price_and_airline() -> None:
    opt = _map_raw_to_flight_option(_RAW_FLIGHT, _WINDOW, CabinClass.ECONOMY)
    assert opt.price_inr == 47500
    assert opt.airline_code == "6E"
    assert opt.flight_number == "6E-1452"


def test_map_raw_computes_outbound_arrival() -> None:
    opt = _map_raw_to_flight_option(_RAW_FLIGHT, _WINDOW, CabinClass.ECONOMY)
    # 02:30 + 510 min (8h30m) = 11:00 same day (IST)
    assert "11:00:00" in opt.outbound_arrival_at


def test_map_raw_computes_return_arrival() -> None:
    opt = _map_raw_to_flight_option(_RAW_FLIGHT, _WINDOW, CabinClass.ECONOMY)
    assert opt.return_arrival_at is not None
    # 10:00 + 510 min = 18:30 IST
    assert "18:30:00" in opt.return_arrival_at


def test_map_raw_transfers_become_layover_count() -> None:
    opt = _map_raw_to_flight_option(_RAW_FLIGHT, _WINDOW, CabinClass.ECONOMY)
    assert opt.layover_count == 2


def test_map_raw_one_way_has_no_return_arrival() -> None:
    one_way = {**_RAW_FLIGHT, "return_at": None, "duration_back": None}
    opt = _map_raw_to_flight_option(one_way, _WINDOW, CabinClass.ECONOMY)
    assert opt.return_departure_at is None
    assert opt.return_arrival_at is None


def test_map_raw_preserves_raw_dict() -> None:
    opt = _map_raw_to_flight_option(_RAW_FLIGHT, _WINDOW, CabinClass.ECONOMY)
    assert opt.raw["price"] == 47500
    assert opt.raw["link"] == "/search/BOM0106CDG08062026"


def test_map_raw_uses_requested_cabin_class() -> None:
    opt = _map_raw_to_flight_option(_RAW_FLIGHT, _WINDOW, CabinClass.BUSINESS)
    assert opt.cabin_class == CabinClass.BUSINESS


# ── agent behaviour tests ─────────────────────────────────────────────────────


async def test_uses_adapter_when_injected() -> None:
    adapter = _mock_adapter([_RAW_FLIGHT])
    agent = FlightHunterAgent(adapter=adapter)
    state = _make_state()
    result = await agent.run(state)
    adapter.get_flights.assert_called_once_with("BOM", "CDG", "2026-06")
    assert len(result.flight_options) == 1


async def test_adapter_result_mapped_to_flight_option() -> None:
    adapter = _mock_adapter([_RAW_FLIGHT])
    agent = FlightHunterAgent(adapter=adapter)
    state = _make_state()
    result = await agent.run(state)
    opt = result.flight_options[0]
    assert opt.price_inr == 47500
    assert opt.airline_code == "6E"
    assert opt.provider == "aviasales"


async def test_falls_back_to_synthetic_without_adapter() -> None:
    agent = FlightHunterAgent(adapter=None)
    state = _make_state(
        intent=_make_intent(origin="BOM", destination="CDG"),
        windows=[_WINDOW],
    )
    result = await agent.run(state)
    # SyntheticProvider has templates for BOM-CDG
    assert len(result.flight_options) > 0
    assert all(opt.provider == "synthetic" for opt in result.flight_options)


async def test_tracks_call_count_per_month() -> None:
    adapter = _mock_adapter([_RAW_FLIGHT])
    agent = FlightHunterAgent(adapter=adapter)
    # Span two calendar months so two API calls are issued
    windows = [
        Window(start_date=date(2026, 6, 15), end_date=date(2026, 6, 21)),
        Window(start_date=date(2026, 7, 1), end_date=date(2026, 7, 15)),
    ]
    state = _make_state(
        intent=_make_intent(earliest=date(2026, 6, 15), latest=date(2026, 7, 15)),
        windows=windows,
    )
    result = await agent.run(state)
    assert result.call_budget.flight_calls_used == 2


async def test_respects_flight_call_budget() -> None:
    adapter = _mock_adapter([_RAW_FLIGHT])
    agent = FlightHunterAgent(adapter=adapter)
    # Span two calendar months; budget allows only 1 more call (max=10, used=9)
    windows = [
        Window(start_date=date(2026, 6, 15), end_date=date(2026, 6, 21)),
        Window(start_date=date(2026, 7, 1), end_date=date(2026, 7, 15)),
    ]
    state = _make_state(
        intent=_make_intent(earliest=date(2026, 6, 15), latest=date(2026, 7, 15)),
        windows=windows,
        budget_overrides={"flight_calls_used": 9},
    )
    result = await agent.run(state)
    assert result.call_budget.flight_calls_used == 10
    assert result.is_partial is True


async def test_no_intent_returns_unchanged() -> None:
    agent = FlightHunterAgent()
    state = RequestState()
    result = await agent.run(state)
    assert result.flight_options == []
    assert result.call_budget.flight_calls_used == 0


async def test_no_windows_returns_unchanged() -> None:
    agent = FlightHunterAgent()
    state = RequestState()
    state.intent = _make_intent()
    state.candidate_windows = []
    result = await agent.run(state)
    assert result.flight_options == []


async def test_multiple_months_accumulate_flights() -> None:
    raw_a = {**_RAW_FLIGHT, "departure_at": "2026-06-15T02:30:00+05:30"}
    raw_b = {**_RAW_FLIGHT, "departure_at": "2026-07-01T02:30:00+05:30"}
    adapter = MagicMock()
    adapter.get_flights = AsyncMock(side_effect=[[raw_a], [raw_b]])
    agent = FlightHunterAgent(adapter=adapter)
    windows = [
        Window(start_date=date(2026, 6, 15), end_date=date(2026, 6, 21)),
        Window(start_date=date(2026, 7, 1), end_date=date(2026, 7, 7)),
    ]
    state = _make_state(
        intent=_make_intent(earliest=date(2026, 6, 15), latest=date(2026, 7, 15)),
        windows=windows,
    )
    result = await agent.run(state)
    assert len(result.flight_options) == 2


async def test_result_is_same_state_object() -> None:
    agent = FlightHunterAgent()
    state = _make_state()
    result = await agent.run(state)
    assert result is state
