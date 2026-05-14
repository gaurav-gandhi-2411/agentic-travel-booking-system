"""vLLM adapter — self-hosted model serving via OpenAI-compatible API.

vLLM exposes an OpenAI-compatible endpoint at /v1/chat/completions.
Configure VLLM_BASE_URL to point at the running vLLM instance.
VLLM_API_KEY defaults to "EMPTY" (vLLM's conventional placeholder when
auth is disabled — change if your deployment enables API key auth).
"""

from __future__ import annotations

import os
from typing import Any

import openai

from travel_agent.llm._openai_compat import openai_compat_chat
from travel_agent.llm.base import LLMResponse, Message, ToolDefinition


class VLLMAdapter:
    """vLLM adapter — self-hosted serving for fine-tuned or large open-weight models."""

    def __init__(self, base_url: str | None = None) -> None:
        _raw = (
            base_url
            if base_url is not None
            else os.environ.get("VLLM_BASE_URL", "http://localhost:8000")
        )
        base = _raw.rstrip("/")
        api_key = os.environ.get("VLLM_API_KEY", "EMPTY")
        self._client = openai.AsyncOpenAI(
            base_url=f"{base}/v1",
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
