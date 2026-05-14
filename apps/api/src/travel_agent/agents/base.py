"""Agent Protocol shared by all agents in the coordinator pipeline."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from travel_agent.coordinator.state import RequestState


@runtime_checkable
class Agent(Protocol):
    async def run(self, state: RequestState) -> RequestState: ...
