"""OpenRouter adapter — cloud fallback for the 'free' routing profile.

OpenRouter aggregates free-tier models (Qwen2.5-72B, Llama3.3-70B, etc.).
Requires OPENROUTER_API_KEY. Optional headers OPENROUTER_SITE_URL and
OPENROUTER_APP_NAME are sent for rate-limit attribution.
"""
from __future__ import annotations

import os
from typing import Any

import openai

from travel_agent.llm._openai_compat import openai_compat_chat
from travel_agent.llm.base import LLMResponse, Message, ToolDefinition

_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAdapter:
    """OpenRouter free-tier adapter — default provider for the 'free' routing profile."""

    def __init__(self) -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            msg = (
                "OpenRouterAdapter requires OPENROUTER_API_KEY. "
                "Set LLM_ROUTING_PROFILE=free and provide OPENROUTER_API_KEY to enable."
            )
            raise RuntimeError(msg)
        self._client = openai.AsyncOpenAI(
            base_url=_BASE_URL,
            api_key=api_key,
            default_headers={
                "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", ""),
                "X-Title": os.environ.get("OPENROUTER_APP_NAME", "DealHunter"),
            },
        )

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return await openai_compat_chat(
            self._client,
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            tools=tools,
        )
