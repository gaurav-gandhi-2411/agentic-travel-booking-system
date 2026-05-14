"""Ollama adapter — local development using the OpenAI-compatible /v1 endpoint.

Ollama serves models at http://localhost:11434/v1/chat/completions by default.
Override the base URL with OLLAMA_BASE_URL for non-standard setups (Docker, remote).
No API key is required; Ollama accepts any non-empty string as the key.
"""
from __future__ import annotations

import os
from typing import Any

import openai

from travel_agent.llm._openai_compat import openai_compat_chat
from travel_agent.llm.base import LLMResponse, Message, ToolDefinition


class OllamaAdapter:
    """Local Ollama adapter — default provider for the 'local' routing profile."""

    def __init__(self, base_url: str | None = None) -> None:
        _raw = (
            base_url
            if base_url is not None
            else os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        )
        base = _raw.rstrip("/")
        self._client = openai.AsyncOpenAI(
            base_url=f"{base}/v1",
            api_key="ollama",
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
