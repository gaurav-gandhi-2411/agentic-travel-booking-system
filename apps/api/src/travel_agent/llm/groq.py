from __future__ import annotations

from typing import Any

from travel_agent.llm.base import LLMResponse, Message


class GroqAdapter:
    """Groq free-tier adapter — fallback when OpenRouter is rate-limited. Phase 2.5 implementation target."""

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
        raise NotImplementedError("GroqAdapter.chat — implemented in Phase 2.5")
