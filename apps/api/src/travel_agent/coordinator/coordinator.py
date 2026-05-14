"""Coordinator orchestrates the multi-agent pipeline for a single user request.

Phase flow: PLANNING → SEARCHING (parallel flight+hotel) → OPTIMIZING → PRESENTING

Phase B: PLANNING is skipped -- callers must pre-populate state.intent.
Phases C-F will wire PlannerAgent, OptimizerAgent scoring, and BookingAgent.

References: ADR-0001 (coordinator pattern), ADR-0005 (window search).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from travel_agent.agents.flight_hunter import FlightHunterAgent
from travel_agent.agents.hotel_hunter import HotelHunterAgent
from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.coordinator.constants import MAX_WINDOWS, WINDOW_SIZE_DAYS
from travel_agent.coordinator.state import (
    CoordinatorPhase,
    RequestState,
    TravelIntent,
    Window,
)


def _generate_windows(intent: TravelIntent) -> list[Window]:
    """Generate up to MAX_WINDOWS 7-day windows stepping daily (ADR-0005)."""
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


class Coordinator:
    def __init__(self) -> None:
        self._flight_agent = FlightHunterAgent()
        self._hotel_agent = HotelHunterAgent()
        self._optimizer = OptimizerAgent()

    async def run(self, state: RequestState) -> RequestState:
        state = state.model_copy(deep=True)

        if state.intent is None:
            state.phase = CoordinatorPhase.ERROR
            state.errors.append("intent must be pre-populated before calling coordinator")
            return state

        try:
            state.candidate_windows = _generate_windows(state.intent)

            state.phase = CoordinatorPhase.SEARCHING
            state = await self._searching_phase(state)

            state.phase = CoordinatorPhase.OPTIMIZING
            state = await self._optimizer.run(state)

            state.phase = CoordinatorPhase.PRESENTING
        except Exception as exc:
            state.phase = CoordinatorPhase.ERROR
            state.errors.append(str(exc))

        return state

    async def _searching_phase(self, state: RequestState) -> RequestState:
        flight_state = state.model_copy(deep=True)
        hotel_state = state.model_copy(deep=True)

        flight_result, hotel_result = await asyncio.gather(
            self._flight_agent.run(flight_state),
            self._hotel_agent.run(hotel_state),
        )

        merged = state.model_copy(deep=True)
        merged.flight_options = flight_result.flight_options
        merged.hotel_options = hotel_result.hotel_options
        merged.call_budget.flight_calls_used = flight_result.call_budget.flight_calls_used
        merged.call_budget.hotel_calls_used = hotel_result.call_budget.hotel_calls_used
        merged.is_partial = flight_result.is_partial or hotel_result.is_partial
        return merged
