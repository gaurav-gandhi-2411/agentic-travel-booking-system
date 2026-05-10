from __future__ import annotations

import os
from typing import Any

from travel_agent.llm.base import LLMResponse, Message


class AnthropicAdapter:
    """Anthropic adapter — eval baseline only. Off by default. Phase 2.5 implementation target.

    Raises RuntimeError on instantiation if ANTHROPIC_API_KEY is not set, preventing
    accidental API spend in local and CI environments.
    """

    def __init__(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            msg = (
                "AnthropicAdapter requires ANTHROPIC_API_KEY. "
                "This adapter is for eval baselines only and is off by default. "
                "Set LLM_ROUTING_PROFILE=eval and provide ANTHROPIC_API_KEY to enable."
            )
            raise RuntimeError(msg)

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
        raise NotImplementedError("AnthropicAdapter.chat — implemented in Phase 2.5")
