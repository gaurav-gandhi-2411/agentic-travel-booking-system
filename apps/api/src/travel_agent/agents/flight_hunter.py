"""FlightHunterAgent — searches for flights across candidate windows.

Uses AviasalesAdapter when injected (month-granularity calls + Python date filter);
falls back to SyntheticProvider for development / tests that do not configure a live
API key.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from travel_agent.coordinator.state import CabinClass, FlightOption, RequestState, Window
from travel_agent.providers.aviasales import AviasalesAdapter
from travel_agent.providers.synthetic import SyntheticProvider

_DECEMBER = 12


def _months_in_range(start: date, end: date) -> list[str]:
    """Return 'YYYY-MM' strings for all calendar months overlapping [start, end]."""
    months: list[str] = []
    current = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)
    while current <= end_month:
        months.append(current.strftime("%Y-%m"))
        if current.month == _DECEMBER:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _date_from_raw(raw: dict[str, Any]) -> date:
    """Extract departure date from a raw Aviasales response dict."""
    return date.fromisoformat(str(raw["departure_at"])[:10])


def _assign_window(departure: date, windows: list[Window]) -> Window:
    """Return the window whose date range contains *departure*, or the last window."""
    for window in windows:
        if window.start_date <= departure <= window.end_date:
            return window
    return windows[-1]


def _map_raw_to_flight_option(
    raw: dict[str, Any],
    window: Window,
    cabin_class: CabinClass,
) -> FlightOption:
    depart_dt = datetime.fromisoformat(str(raw["departure_at"]))
    arrive_dt = depart_dt + timedelta(minutes=int(raw["duration_to"]))

    return_depart = raw.get("return_at")
    return_arrive: str | None = None
    return_duration = raw.get("duration_back")
    if return_depart is not None and return_duration is not None:
        return_arrive = (
            datetime.fromisoformat(str(return_depart)) + timedelta(minutes=int(return_duration))
        ).isoformat()

    return FlightOption(
        window=window,
        provider="aviasales",
        origin_iata=str(raw["origin"]),
        destination_iata=str(raw["destination"]),
        airline_code=str(raw["airline"]),
        flight_number=f"{raw['airline']}-{raw['flight_number']}",
        cabin_class=cabin_class,
        price_inr=int(raw["price"]),
        outbound_departure_at=str(raw["departure_at"]),
        outbound_arrival_at=arrive_dt.isoformat(),
        return_departure_at=str(return_depart) if return_depart is not None else None,
        return_arrival_at=return_arrive,
        outbound_duration_minutes=int(raw["duration_to"]),
        return_duration_minutes=int(return_duration) if return_duration is not None else None,
        layover_count=int(raw.get("transfers", 0)),
        is_refundable=bool(raw.get("is_refundable", False)),
        raw=dict(raw),
    )


class FlightHunterAgent:
    def __init__(self, adapter: AviasalesAdapter | None = None) -> None:
        self._adapter = adapter
        self._synthetic = SyntheticProvider()

    async def run(self, state: RequestState) -> RequestState:
        if state.intent is None or not state.candidate_windows:
            return state

        all_flights: list[FlightOption] = []

        if self._adapter is not None:
            # Month-granularity: one Aviasales call per calendar month, then
            # filter to the exact horizon in Python.
            months = _months_in_range(
                state.intent.earliest_departure,
                state.intent.latest_departure,
            )
            for month in months:
                if not state.call_budget.can_call_flight():
                    state.is_partial = True
                    break
                raw_flights = await self._adapter.get_flights(
                    state.intent.origin_iata,
                    state.intent.destination_iata,
                    month,
                )
                for r in raw_flights:
                    dep = _date_from_raw(r)
                    if state.intent.earliest_departure <= dep <= state.intent.latest_departure:
                        window = _assign_window(dep, state.candidate_windows)
                        all_flights.append(
                            _map_raw_to_flight_option(r, window, state.intent.cabin_class)
                        )
                state.call_budget.flight_calls_used += 1
        else:
            # Synthetic provider — per-window calls (no live API key needed).
            for window in state.candidate_windows:
                if not state.call_budget.can_call_flight():
                    state.is_partial = True
                    break
                flights = self._synthetic.get_flights(
                    state.intent.origin_iata,
                    state.intent.destination_iata,
                    window,
                )
                all_flights.extend(flights)
                state.call_budget.flight_calls_used += 1

        state.flight_options = all_flights
        return state
