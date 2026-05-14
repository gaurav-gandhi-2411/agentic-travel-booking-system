from __future__ import annotations

from typing import Any

from travel_agent.llm.base import LLMResponse, Message


class OllamaAdapter:
    """Local Ollama adapter — default for development."""

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
        msg = "OllamaAdapter.chat — implemented in Phase A"
        raise NotImplementedError(msg)
