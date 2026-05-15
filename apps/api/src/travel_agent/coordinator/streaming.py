"""Streaming coordinator — async generator that yields SSE events.

The non-streaming Coordinator.run() remains for unit tests and non-SSE callers.
This module owns the per-month flight-search loop so search_progress events
can be emitted between API calls (the batch FlightHunterAgent doesn't yield mid-run).

Event sequence:
  planner_started
  planner_done          {intent: TravelIntent}
  search_started        {windows: [{start, end}]}
  search_progress       {window_idx: int, flights_found: int}   (one per month)
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

from travel_agent.agents.flight_hunter import (
    _assign_window,
    _date_from_raw,
    _map_raw_to_flight_option,
    _months_in_range,
)
from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.api.cache import search_cache
from travel_agent.coordinator.constants import MAX_WINDOWS, WINDOW_SIZE_DAYS
from travel_agent.coordinator.state import (
    CoordinatorPhase,
    FlightOption,
    RequestState,
    TravelIntent,
    TripType,
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
        current += timedelta(days=WINDOW_SIZE_DAYS)
    return windows


async def stream_search(  # noqa: PLR0912, PLR0915
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
        "windows": [{"start": str(w.start_date), "end": str(w.end_date)} for w in windows],
    }

    adapter = _get_adapter()
    all_flights: list[FlightOption] = []
    state.phase = CoordinatorPhase.SEARCHING

    intent = state.intent
    is_round_trip = intent.trip_type == TripType.ROUND_TRIP

    if adapter is not None:
        # Month-granularity: one call per calendar month, then filter to horizon.
        # Pass return_at when round-trip so Aviasales prices include the return leg.
        return_month: str | None = None
        if is_round_trip:
            return_date = intent.earliest_departure + timedelta(days=intent.trip_duration_days)
            return_month = return_date.strftime("%Y-%m")

        months = _months_in_range(intent.earliest_departure, intent.latest_departure)
        for idx, month in enumerate(months):
            if not state.call_budget.can_call_flight():
                state.is_partial = True
                break
            try:
                raw_flights = await adapter.get_flights(
                    intent.origin_iata,
                    intent.destination_iata,
                    month,
                    return_at=return_month,
                )
            except Exception as exc:
                yield {"type": "error", "message": f"Flight search error (month {month}): {exc}"}
                return
            month_flights: list[FlightOption] = []
            for r in raw_flights:
                dep = _date_from_raw(r)
                if intent.earliest_departure <= dep <= intent.latest_departure:
                    window = _assign_window(dep, windows)
                    month_flights.append(_map_raw_to_flight_option(r, window, intent.cabin_class))
            all_flights.extend(month_flights)
            state.call_budget.flight_calls_used += 1
            yield {
                "type": "search_progress",
                "window_idx": idx,
                "flights_found": len(month_flights),
            }
    else:
        # Synthetic provider — per-window calls; pass trip_type and duration.
        for idx, window in enumerate(windows):
            if not state.call_budget.can_call_flight():
                state.is_partial = True
                break
            try:
                window_flights = _synthetic.get_flights(
                    intent.origin_iata,
                    intent.destination_iata,
                    window,
                    trip_type=intent.trip_type,
                    trip_duration_days=intent.trip_duration_days,
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
                f"No flights found for {intent.origin_iata} → "
                f"{intent.destination_iata}. "
                "Try a different date range or route."
            ),
        }
        return

    state.flight_options = all_flights

    # Cache for /refine — keyed by request_id, no external deps needed
    request_id = str(state.request_id)
    await search_cache.put(request_id, intent, all_flights)

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
    yield {"type": "done", "request_id": request_id}


def _get_adapter() -> AviasalesAdapter | None:
    """Return AviasalesAdapter if APP_MODE=demo and key is set, else None."""
    if os.environ.get("APP_MODE") != "demo":
        return None
    try:
        return AviasalesAdapter()
    except RuntimeError:
        return None
