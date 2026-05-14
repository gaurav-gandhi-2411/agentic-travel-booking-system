from __future__ import annotations

from typing import Any

from travel_agent.llm.base import LLMResponse, Message


class OpenRouterAdapter:
    """OpenRouter free-tier adapter — default for cloud deployment."""

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        msg = "OpenRouterAdapter.chat — implemented in Phase A"
        raise NotImplementedError(msg)
