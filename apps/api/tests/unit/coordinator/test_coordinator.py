"""End-to-end coordinator tests for Phase B skeleton.

These tests use a pre-built TravelIntent (no PlannerAgent) and verify that
the coordinator drives the state machine through SEARCHING → OPTIMIZING →
PRESENTING, populates flight and hotel options, and correctly merges parallel
agent results.
"""
from __future__ import annotations

from datetime import date

from travel_agent.coordinator.constants import MAX_WINDOWS, WINDOW_SIZE_DAYS
from travel_agent.coordinator.coordinator import Coordinator, _generate_windows
from travel_agent.coordinator.state import (
    CoordinatorPhase,
    RequestState,
    TravelIntent,
    Window,
)


def _make_intent(
    origin: str = "BOM",
    destination: str = "CDG",
    earliest: date = date(2026, 6, 1),
    latest: date = date(2026, 6, 30),
) -> TravelIntent:
    return TravelIntent(
        origin_iata=origin,
        destination_iata=destination,
        earliest_departure=earliest,
        latest_departure=latest,
    )


# ── window generation ─────────────────────────────────────────────────────────


def test_generate_windows_respects_max() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 30))
    windows = _generate_windows(intent)
    assert len(windows) == MAX_WINDOWS


def test_generate_windows_step_by_one_day() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 30))
    windows = _generate_windows(intent)
    for i in range(1, len(windows)):
        delta = (windows[i].start_date - windows[i - 1].start_date).days
        assert delta == 1, f"Window step should be 1 day, got {delta}"


def test_generate_windows_correct_size() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 30))
    windows = _generate_windows(intent)
    for w in windows:
        size = (w.end_date - w.start_date).days + 1
        assert size == WINDOW_SIZE_DAYS, f"Expected {WINDOW_SIZE_DAYS}-day window, got {size}"


def test_generate_windows_short_horizon() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 3))
    windows = _generate_windows(intent)
    assert len(windows) == 3


def test_generate_windows_single_day_horizon() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 1))
    windows = _generate_windows(intent)
    assert len(windows) == 1
    assert windows[0].start_date == date(2026, 6, 1)


# ── coordinator happy path ────────────────────────────────────────────────────


async def test_coordinator_reaches_presenting_phase() -> None:
    state = RequestState(intent=_make_intent())
    result = await Coordinator().run(state)
    assert result.phase == CoordinatorPhase.PRESENTING


async def test_coordinator_populates_flight_options() -> None:
    state = RequestState(intent=_make_intent())
    result = await Coordinator().run(state)
    assert len(result.flight_options) > 0


async def test_coordinator_populates_hotel_options() -> None:
    state = RequestState(intent=_make_intent())
    result = await Coordinator().run(state)
    assert len(result.hotel_options) > 0


async def test_coordinator_flight_options_match_route() -> None:
    state = RequestState(intent=_make_intent(origin="BOM", destination="CDG"))
    result = await Coordinator().run(state)
    for flight in result.flight_options:
        assert flight.origin_iata == "BOM"
        assert flight.destination_iata == "CDG"


async def test_coordinator_hotel_options_in_correct_city() -> None:
    state = RequestState(intent=_make_intent(destination="CDG"))
    result = await Coordinator().run(state)
    for hotel in result.hotel_options:
        assert hotel.city == "Paris"


async def test_coordinator_candidate_windows_populated() -> None:
    state = RequestState(intent=_make_intent())
    result = await Coordinator().run(state)
    assert len(result.candidate_windows) == MAX_WINDOWS


# ── no-intent guard ───────────────────────────────────────────────────────────


async def test_coordinator_errors_without_intent() -> None:
    state = RequestState()
    result = await Coordinator().run(state)
    assert result.phase == CoordinatorPhase.ERROR
    assert result.errors


# ── call budget tracking ──────────────────────────────────────────────────────


async def test_coordinator_increments_flight_call_budget() -> None:
    state = RequestState(intent=_make_intent())
    result = await Coordinator().run(state)
    assert result.call_budget.flight_calls_used == MAX_WINDOWS


async def test_coordinator_increments_hotel_call_budget() -> None:
    state = RequestState(intent=_make_intent())
    result = await Coordinator().run(state)
    assert result.call_budget.hotel_calls_used == MAX_WINDOWS


# ── state isolation (model_copy) ──────────────────────────────────────────────


async def test_coordinator_does_not_mutate_input_state() -> None:
    intent = _make_intent()
    original = RequestState(intent=intent)
    original_phase = original.phase
    await Coordinator().run(original)
    assert original.phase == original_phase
    assert original.flight_options == []


# ── unknown destination ───────────────────────────────────────────────────────


async def test_coordinator_unknown_destination_returns_empty_hotels() -> None:
    intent = TravelIntent(
        origin_iata="BOM",
        destination_iata="LHR",  # not in IATA_TO_CITY
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 30),
    )
    state = RequestState(intent=intent)
    result = await Coordinator().run(state)
    assert result.hotel_options == []
    assert result.phase == CoordinatorPhase.PRESENTING


# ── window type check ─────────────────────────────────────────────────────────


def test_window_is_pydantic_model() -> None:
    w = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))
    assert isinstance(w, Window)
