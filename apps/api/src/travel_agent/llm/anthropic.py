"""Anthropic adapter — primary LLM provider for the eval and prod routing profiles.

Eval profile: manual baseline runs against claude-sonnet-4-6.
Prod profile: B2B tenants supply their own ANTHROPIC_API_KEY.

Prompt caching: pass cache_system_prompt=True in **kwargs to wrap the system
prompt in a cache_control block. Saves ~90% of system-prompt tokens on calls
that hit the 5-minute cache window. Enable on agents with long, stable prompts.
"""

from __future__ import annotations

import os
import time
from typing import Any

import anthropic

from travel_agent.llm._tool_translation import parse_anthropic_tool_calls, to_anthropic_tools
from travel_agent.llm.base import LLMError, LLMResponse, Message, ToolDefinition


class AnthropicAdapter:
    """Async Anthropic Messages API wrapper with tool_use support."""

    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            msg = (
                "AnthropicAdapter requires ANTHROPIC_API_KEY. "
                "Active for 'eval' and 'prod' routing profiles. "
                "Set LLM_ROUTING_PROFILE=eval|prod and provide ANTHROPIC_API_KEY to enable."
            )
            raise RuntimeError(msg)
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

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
        start = time.monotonic()

        api_messages: list[dict[str, Any]] = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        # Optionally enable prompt caching on the system prompt via cache_control.
        # Pass cache_system_prompt=True in **kwargs at call sites that warrant caching
        # (long, stable agent system prompts). Ephemeral TTL: 5 minutes.
        system_param: str | list[dict[str, Any]] | None = system
        if system and kwargs.get("cache_system_prompt"):
            system_param = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        tools_param: list[dict[str, Any]] | None = to_anthropic_tools(tools) if tools else None

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_param is not None:
            create_kwargs["system"] = system_param
        if tools_param is not None:
            create_kwargs["tools"] = tools_param

        try:
            response = await self._client.messages.create(**create_kwargs)
        except anthropic.APIError as exc:
            raise LLMError(str(exc)) from exc

        latency_ms = (time.monotonic() - start) * 1000

        content_text = next((block.text for block in response.content if block.type == "text"), "")
        tool_calls = parse_anthropic_tool_calls(list(response.content))

        return LLMResponse(
            content=content_text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
        )
