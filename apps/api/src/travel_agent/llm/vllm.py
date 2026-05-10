"""vLLM adapter stub — future implementation for serving fine-tuned checkpoints at scale."""
from __future__ import annotations

from typing import Any

from travel_agent.llm.base import LLMResponse, Message


class VLLMAdapter:
    """vLLM adapter — future implementation target when self-hosted model serving is required."""

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
        raise NotImplementedError("VLLMAdapter — implemented when self-hosted serving is required")
