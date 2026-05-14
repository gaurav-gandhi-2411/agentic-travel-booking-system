"""FlightHunterAgent — searches SyntheticProvider across candidate windows."""
from __future__ import annotations

from travel_agent.coordinator.state import FlightOption, RequestState
from travel_agent.providers.synthetic import SyntheticProvider


class FlightHunterAgent:
    def __init__(self) -> None:
        self._provider = SyntheticProvider()

    async def run(self, state: RequestState) -> RequestState:
        if state.intent is None or not state.candidate_windows:
            return state

        all_flights: list[FlightOption] = []
        for window in state.candidate_windows:
            if not state.call_budget.can_call_flight():
                state.is_partial = True
                break
            flights = self._provider.get_flights(
                state.intent.origin_iata,
                state.intent.destination_iata,
                window,
            )
            all_flights.extend(flights)
            state.call_budget.flight_calls_used += 1

        state.flight_options = all_flights
        return state
