"""NVIDIA NIM adapter — OpenAI-compatible inference via integrate.api.nvidia.com.

NIM exposes an OpenAI-compatible endpoint, so this adapter delegates to
openai_compat_chat with NIM's base URL and NVIDIA_API_KEY.
Used as a fallback provider when Groq hits TPM/TPD quota limits.
"""

from __future__ import annotations

import os
from typing import Any

import openai

from travel_agent.llm._openai_compat import openai_compat_chat
from travel_agent.llm.base import LLMResponse, Message, ToolDefinition

_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NIMAdapter:
    """NVIDIA NIM adapter — fallback when Groq is rate-limited."""

    def __init__(self) -> None:
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            msg = "NIMAdapter requires NVIDIA_API_KEY to be set."
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
        )
