from __future__ import annotations

from typing import Any

from travel_agent.llm.base import LLMResponse, Message


class GroqAdapter:
    """Groq free-tier adapter — fallback when OpenRouter is rate-limited."""

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
        msg = "GroqAdapter.chat — implemented in Phase A"
        raise NotImplementedError(msg)
