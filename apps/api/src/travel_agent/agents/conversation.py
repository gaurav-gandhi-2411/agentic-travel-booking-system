"""ConversationManagerAgent stub — multi-turn dialogue lands in Phase F."""

from __future__ import annotations

from travel_agent.coordinator.state import RequestState


class ConversationManagerAgent:
    async def run(self, state: RequestState) -> RequestState:
        msg = "ConversationManagerAgent lands in Phase F"
        raise NotImplementedError(msg)
