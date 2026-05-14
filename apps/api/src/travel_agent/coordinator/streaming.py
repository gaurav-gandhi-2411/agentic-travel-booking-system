"""Streaming coordinator — async generator that yields SSE events.

The non-streaming Coordinator.run() remains for unit tests and non-SSE callers.
This module owns the per-window flight-search loop so search_progress events
can be emitted between windows (the batch FlightHunterAgent doesn't yield mid-run).

Event sequence:
  planner_started
  planner_done          {intent: TravelIntent}
  search_started        {windows: [{start, end}]}
  search_progress       {window_idx: int, flights_found: int}   (one per window)
  search_done           {total_options: int}
  optimizer_started
  archetype_ready       {archetype: Archetype}                  (twice)
  done
  error                 {message: str}                          (on failure)
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import timedelta

from travel_agent.agents.flight_hunter import _map_raw_to_flight_option
from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.constants import MAX_WINDOWS, WINDOW_SIZE_DAYS
from travel_agent.coordinator.state import (
    CoordinatorPhase,
    FlightOption,
    RequestState,
    TravelIntent,
    Window,
)
from travel_agent.providers.aviasales import AviasalesAdapter
from travel_agent.providers.synthetic import SyntheticProvider

_synthetic = SyntheticProvider()


def _generate_windows(intent: TravelIntent) -> list[Window]:
    windows: list[Window] = []
    current = intent.earliest_departure
    while current <= intent.latest_departure and len(windows) < MAX_WINDOWS:
        windows.append(
            Window(
                start_date=current,
                end_date=current + timedelta(days=WINDOW_SIZE_DAYS - 1),
            )
        )
        current += timedelta(days=1)
    return windows


async def stream_search(
    query: str,
    planner: PlannerAgent,
    optimizer: OptimizerAgent,
) -> AsyncGenerator[dict[str, object], None]:
    """Async generator: run the full agent pipeline and yield SSE event dicts."""
    # ── Planner ────────────────────────────────────────────────────────────────
    yield {"type": "planner_started"}

    state = RequestState(raw_input=query)
    try:
        state = await planner.run(state)
    except Exception as exc:
        yield {"type": "error", "message": f"Planner failed: {exc}"}
        return

    if state.phase == CoordinatorPhase.ERROR or state.intent is None:
        msg = state.errors[0] if state.errors else "PlannerAgent returned no intent"
        yield {"type": "error", "message": msg}
        return

    yield {"type": "planner_done", "intent": state.intent.model_dump(mode="json")}

    # ── Window search ──────────────────────────────────────────────────────────
    windows = _generate_windows(state.intent)
    state.candidate_windows = windows

    yield {
        "type": "search_started",
        "windows": [
            {"start": str(w.start_date), "end": str(w.end_date)} for w in windows
        ],
    }

    adapter = _get_adapter()
    all_flights: list[FlightOption] = []
    state.phase = CoordinatorPhase.SEARCHING

    for idx, window in enumerate(windows):
        if not state.call_budget.can_call_flight():
            state.is_partial = True
            break
        try:
            if adapter is not None:
                raw_flights = await adapter.get_flights(
                    state.intent.origin_iata,
                    state.intent.destination_iata,
                    window.start_date.isoformat(),
                )
                window_flights: list[FlightOption] = [
                    _map_raw_to_flight_option(r, window, state.intent.cabin_class)
                    for r in raw_flights
                ]
            else:
                window_flights = _synthetic.get_flights(
                    state.intent.origin_iata,
                    state.intent.destination_iata,
                    window,
                )
        except Exception as exc:
            yield {"type": "error", "message": f"Flight search error (window {idx}): {exc}"}
            return

        all_flights.extend(window_flights)
        state.call_budget.flight_calls_used += 1
        yield {
            "type": "search_progress",
            "window_idx": idx,
            "flights_found": len(window_flights),
        }

    if not all_flights:
        yield {
            "type": "error",
            "message": (
                f"No flights found for {state.intent.origin_iata} → "
                f"{state.intent.destination_iata}. "
                "Try a different date range or route."
            ),
        }
        return

    state.flight_options = all_flights
    yield {"type": "search_done", "total_options": len(all_flights)}

    # ── Optimizer ──────────────────────────────────────────────────────────────
    yield {"type": "optimizer_started"}
    state.phase = CoordinatorPhase.OPTIMIZING

    try:
        state = await optimizer.run(state)
    except Exception as exc:
        yield {"type": "error", "message": f"Optimizer failed: {exc}"}
        return

    for archetype in state.archetypes:
        yield {"type": "archetype_ready", "archetype": archetype.model_dump(mode="json")}

    state.phase = CoordinatorPhase.PRESENTING
    yield {"type": "done"}


def _get_adapter() -> AviasalesAdapter | None:
    """Return AviasalesAdapter if APP_MODE=demo and key is set, else None."""
    if os.environ.get("APP_MODE") != "demo":
        return None
    try:
        return AviasalesAdapter()
    except RuntimeError:
        return None
