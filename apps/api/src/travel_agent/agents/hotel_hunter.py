"""HotelHunterAgent — searches SyntheticProvider for hotels across candidate windows."""

from __future__ import annotations

from travel_agent.coordinator.constants import IATA_TO_CITY
from travel_agent.coordinator.state import HotelOption, RequestState
from travel_agent.providers.synthetic import SyntheticProvider


class HotelHunterAgent:
    def __init__(self) -> None:
        self._provider = SyntheticProvider()

    async def run(self, state: RequestState) -> RequestState:
        if state.intent is None or not state.candidate_windows:
            return state

        city = IATA_TO_CITY.get(state.intent.destination_iata)
        if city is None:
            return state

        nights = state.intent.trip_duration_days
        min_stars = state.intent.hotel_min_stars

        all_hotels: list[HotelOption] = []
        for window in state.candidate_windows:
            if not state.call_budget.can_call_hotel():
                state.is_partial = True
                break
            hotels = self._provider.get_hotels(city, window, nights, min_stars)
            all_hotels.extend(hotels)
            state.call_budget.hotel_calls_used += 1

        state.hotel_options = all_hotels
        return state
