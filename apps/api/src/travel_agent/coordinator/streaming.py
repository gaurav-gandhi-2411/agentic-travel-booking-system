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
from enum import StrEnum

from travel_agent.agents.flight_hunter import (
    _assign_window,
    _date_from_raw,
    _map_raw_to_flight_option,
    _months_in_range,
)
from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.api.cache import search_cache
from travel_agent.coordinator.state import (
    CallBudget,
    CoordinatorPhase,
    FlightOption,
    RequestState,
    TravelIntent,
    TripType,
)
from travel_agent.coordinator.windows import generate_windows
from travel_agent.providers.aviasales import AviasalesAdapter
from travel_agent.providers.synthetic import SyntheticProvider

_synthetic = SyntheticProvider()


class StreamEventType(StrEnum):
    # Existing pipeline events (defined here for reference; emitted as inline strings)
    PLANNER_STARTED = "planner_started"
    PLANNER_DONE = "planner_done"
    SEARCH_STARTED = "search_started"
    SEARCH_PROGRESS = "search_progress"
    SEARCH_DONE = "search_done"
    OPTIMIZER_STARTED = "optimizer_started"
    ARCHETYPE_READY = "archetype_ready"
    DONE = "done"
    ERROR = "error"
    NO_DATA_FOR_ROUTE = "no_data_for_route"
    # Refine-specific legacy event
    REFINE_STARTED = "refine_started"
    # Conversation events — emitted by the /refine route (PR 2)
    CONVERSATION_THINKING = "conversation_thinking"
    CONVERSATION_ACTION_CLASSIFIED = "conversation_action_classified"
    CONVERSATION_MESSAGE = "conversation_message"

# Popular destinations used for route alternatives when no data found.
_POPULAR_DESTINATIONS: list[str] = ["BKK", "DXB", "KUL", "CMB", "SIN", "DEL", "BOM"]
_DESTINATION_NAMES: dict[str, str] = {
    "BKK": "Bangkok",
    "DXB": "Dubai",
    "KUL": "Kuala Lumpur",
    "CMB": "Colombo",
    "SIN": "Singapore",
    "DEL": "Delhi",
    "BOM": "Mumbai",
    "CDG": "Paris",
    "LHR": "London",
    "NRT": "Tokyo",
}


def _suggest_alternatives(origin: str, destination: str) -> list[dict[str, str]]:
    candidates = [d for d in _POPULAR_DESTINATIONS if d not in {destination, origin}]
    origin_name = _DESTINATION_NAMES.get(origin, origin)
    results: list[dict[str, str]] = []
    for dest in candidates[:3]:
        dest_name = _DESTINATION_NAMES.get(dest, dest)
        results.append(
            {
                "origin_iata": origin,
                "destination_iata": dest,
                "label": f"Try {origin_name} to {dest_name}",
            }
        )
    return results


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
    windows = generate_windows(state.intent)
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
            "type": "no_data_for_route",
            "origin_iata": intent.origin_iata,
            "destination_iata": intent.destination_iata,
            "message": (
                f"No flights found for {intent.origin_iata} → {intent.destination_iata} "
                "in our database."
            ),
            "alternatives": _suggest_alternatives(intent.origin_iata, intent.destination_iata),
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


async def stream_replan(  # noqa: PLR0912
    intent: TravelIntent,
    optimizer: OptimizerAgent,
) -> AsyncGenerator[dict[str, object], None]:
    """Async generator: run flight-search using a pre-built TravelIntent.

    Used by the /refine REPLAN path to skip PlannerAgent when ConversationManagerAgent
    has already produced a structured intent update. Emits the same SSE events as
    stream_search minus the planner phase (no planner_started / planner_done).
    """
    windows = generate_windows(intent)
    yield {
        "type": "search_started",
        "windows": [{"start": str(w.start_date), "end": str(w.end_date)} for w in windows],
    }

    adapter = _get_adapter()
    all_flights: list[FlightOption] = []
    call_budget = CallBudget()
    is_round_trip = intent.trip_type == TripType.ROUND_TRIP

    if adapter is not None:
        return_month: str | None = None
        if is_round_trip:
            return_date = intent.earliest_departure + timedelta(days=intent.trip_duration_days)
            return_month = return_date.strftime("%Y-%m")

        months = _months_in_range(intent.earliest_departure, intent.latest_departure)
        for idx, month in enumerate(months):
            if not call_budget.can_call_flight():
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
            call_budget.flight_calls_used += 1
            yield {
                "type": "search_progress",
                "window_idx": idx,
                "flights_found": len(month_flights),
            }
    else:
        for idx, window in enumerate(windows):
            if not call_budget.can_call_flight():
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
            call_budget.flight_calls_used += 1
            yield {
                "type": "search_progress",
                "window_idx": idx,
                "flights_found": len(window_flights),
            }

    if not all_flights:
        yield {
            "type": "no_data_for_route",
            "origin_iata": intent.origin_iata,
            "destination_iata": intent.destination_iata,
            "message": (
                f"No flights found for {intent.origin_iata} → {intent.destination_iata} "
                "in our database."
            ),
            "alternatives": _suggest_alternatives(intent.origin_iata, intent.destination_iata),
        }
        return

    state = RequestState(raw_input="", intent=intent, flight_options=all_flights)
    request_id = str(state.request_id)
    await search_cache.put(request_id, intent, all_flights)

    yield {"type": "search_done", "total_options": len(all_flights)}
    yield {"type": "optimizer_started"}

    try:
        state = await optimizer.run(state)
    except Exception as exc:
        yield {"type": "error", "message": f"Optimizer failed: {exc}"}
        return

    for archetype in state.archetypes:
        yield {"type": "archetype_ready", "archetype": archetype.model_dump(mode="json")}

    yield {"type": "done", "request_id": request_id}


def _get_adapter() -> AviasalesAdapter | None:
    """Return AviasalesAdapter if APP_MODE=demo and key is set, else None."""
    if os.environ.get("APP_MODE") != "demo":
        return None
    try:
        return AviasalesAdapter()
    except RuntimeError:
        return None
