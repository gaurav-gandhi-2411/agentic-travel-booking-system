"""Groq adapter — low-latency inference via Groq Cloud.

Groq exposes an OpenAI-compatible API at api.groq.com.
Requires GROQ_API_KEY. Used as a secondary fallback in the 'free' profile
when OpenRouter is rate-limited, and directly in deployments where sub-200ms
inference latency is required.
"""

from __future__ import annotations

import os
from typing import Any

import openai

from travel_agent.llm._openai_compat import openai_compat_chat
from travel_agent.llm.base import LLMResponse, Message, ToolDefinition

_BASE_URL = "https://api.groq.com/openai/v1"


class GroqAdapter:
    """Groq Cloud adapter — fallback when OpenRouter is rate-limited."""

    def __init__(self) -> None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            msg = "GroqAdapter requires GROQ_API_KEY to be set."
            raise RuntimeError(msg)
        self._client = openai.AsyncOpenAI(
            base_url=_BASE_URL,
            api_key=api_key,
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
            extra_params=kwargs.get("extra_params"),
        )
