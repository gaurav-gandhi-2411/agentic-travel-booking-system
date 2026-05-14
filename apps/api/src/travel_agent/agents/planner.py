"""PlannerAgent stub — LLM-powered intent parsing lands in Phase C."""
from __future__ import annotations

from travel_agent.coordinator.state import RequestState


class PlannerAgent:
    async def run(self, state: RequestState) -> RequestState:
        msg = "PlannerAgent LLM parsing lands in Phase C"
        raise NotImplementedError(msg)
