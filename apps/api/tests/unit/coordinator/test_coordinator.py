"""Coordinator tests — migrated from Coordinator.run() to stream_search() directly.

Coordinator.run() and coordinator.py have been deleted (audit Risk 8).
These tests call stream_search() with pre-built mock agents, exercising the same
pipeline logic without HTTP or a real LLM.

Window generation tests now import from coordinator.windows.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from travel_agent.coordinator.constants import MAX_WINDOWS, WINDOW_SIZE_DAYS
from travel_agent.coordinator.state import (
    CoordinatorPhase,
    RequestState,
    TravelIntent,
    Window,
)
from travel_agent.coordinator.streaming import stream_search
from travel_agent.coordinator.windows import generate_windows


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
    # Long horizon (10 months) so MAX_WINDOWS cap is the binding constraint
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2027, 3, 31))
    windows = generate_windows(intent)
    assert len(windows) == MAX_WINDOWS


def test_generate_windows_step_by_window_size() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 30))
    windows = generate_windows(intent)
    for i in range(1, len(windows)):
        delta = (windows[i].start_date - windows[i - 1].start_date).days
        assert delta == WINDOW_SIZE_DAYS, f"expected {WINDOW_SIZE_DAYS}, got {delta}"


def test_generate_windows_correct_size() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 30))
    windows = generate_windows(intent)
    for w in windows:
        size = (w.end_date - w.start_date).days + 1
        assert size == WINDOW_SIZE_DAYS, f"Expected {WINDOW_SIZE_DAYS}-day window, got {size}"


def test_generate_windows_short_horizon() -> None:
    # 3-day horizon with a 7-day stride produces exactly 1 window
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 3))
    windows = generate_windows(intent)
    assert len(windows) == 1


def test_generate_windows_single_day_horizon() -> None:
    intent = _make_intent(earliest=date(2026, 6, 1), latest=date(2026, 6, 1))
    windows = generate_windows(intent)
    assert len(windows) == 1
    assert windows[0].start_date == date(2026, 6, 1)


def test_window_is_pydantic_model() -> None:
    w = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))
    assert isinstance(w, Window)


# ── stream_search helpers ─────────────────────────────────────────────────────


class _MockPlannerAgent:
    """Pre-built planner that injects a fixed intent."""

    def __init__(self, intent: TravelIntent) -> None:
        self._intent = intent

    async def run(self, state: Any, *, today: Any = None) -> Any:
        state.intent = self._intent
        return state


class _MockOptimizerAgent:
    """Noop optimizer — passes state through unchanged."""

    async def run(self, state: Any, *, today: Any = None) -> Any:
        return state


class _FailingPlannerAgent:
    async def run(self, state: Any, *, today: Any = None) -> Any:
        raise RuntimeError("planner exploded")


class _NoIntentPlannerAgent:
    """Planner that returns state without setting intent."""

    async def run(self, state: Any, *, today: Any = None) -> Any:
        return state  # intent stays None


async def _collect_events(
    query: str,
    intent: TravelIntent,
    optimizer: Any = None,
) -> list[dict[str, Any]]:
    planner = _MockPlannerAgent(intent)
    opt = optimizer or _MockOptimizerAgent()
    events: list[dict[str, Any]] = []
    async for event in stream_search(query, planner, opt):
        events.append(event)
    return events


# ── stream_search pipeline tests ──────────────────────────────────────────────


async def test_stream_search_reaches_done() -> None:
    """BOM→CDG has synthetic data — should reach done or no_data_for_route."""
    intent = _make_intent()
    events = await _collect_events("BOM to CDG in June", intent)
    types = [e["type"] for e in events]
    assert "done" in types or "no_data_for_route" in types


async def test_stream_search_emits_search_progress() -> None:
    intent = _make_intent()
    events = await _collect_events("BOM to CDG in June", intent)
    progress_events = [e for e in events if e["type"] == "search_progress"]
    assert len(progress_events) > 0


async def test_stream_search_emits_planner_started() -> None:
    intent = _make_intent()
    events = await _collect_events("BOM to CDG in June", intent)
    assert events[0]["type"] == "planner_started"


async def test_stream_search_planner_error_emits_error_event() -> None:
    events: list[dict[str, Any]] = []
    async for event in stream_search("test", _FailingPlannerAgent(), _MockOptimizerAgent()):
        events.append(event)
    assert any(e["type"] == "error" for e in events)


async def test_stream_no_intent_emits_error() -> None:
    events: list[dict[str, Any]] = []
    async for event in stream_search("test", _NoIntentPlannerAgent(), _MockOptimizerAgent()):
        events.append(event)
    assert any(e["type"] == "error" for e in events)


async def test_stream_search_candidate_windows_populated() -> None:
    """June 1-30 with a 7-day stride produces 5 windows (not MAX_WINDOWS=12)."""
    intent = _make_intent()
    events = await _collect_events("BOM to CDG in June", intent)
    search_started = next((e for e in events if e["type"] == "search_started"), None)
    assert search_started is not None
    assert len(search_started["windows"]) == 5


async def test_stream_search_flight_options_match_route() -> None:
    intent = _make_intent(origin="BOM", destination="CDG")
    events = await _collect_events("BOM to CDG in June", intent)
    progress_events = [e for e in events if e["type"] == "search_progress"]
    total_found = sum(e["flights_found"] for e in progress_events)
    assert total_found > 0
