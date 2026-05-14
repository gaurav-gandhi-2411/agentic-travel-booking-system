"""BookingAgent stub — HITL booking flow lands in Phase E."""

from __future__ import annotations

from travel_agent.coordinator.state import RequestState


class BookingAgent:
    async def run(self, state: RequestState) -> RequestState:
        msg = "BookingAgent lands in Phase E"
        raise NotImplementedError(msg)
