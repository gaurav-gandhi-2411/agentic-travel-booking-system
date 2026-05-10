from __future__ import annotations

from typing import Any

from travel_agent.llm.base import LLMResponse, Message


class OpenRouterAdapter:
    """OpenRouter free-tier adapter — default for cloud deployment. Phase 2.5 implementation target."""

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        raise NotImplementedError("OpenRouterAdapter.chat — implemented in Phase 2.5")
