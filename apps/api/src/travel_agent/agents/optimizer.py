"""OptimizerAgent stub — Pareto scoring lands in Phase D."""
from __future__ import annotations

from travel_agent.coordinator.state import RequestState


class OptimizerAgent:
    async def run(self, state: RequestState) -> RequestState:
        return state
