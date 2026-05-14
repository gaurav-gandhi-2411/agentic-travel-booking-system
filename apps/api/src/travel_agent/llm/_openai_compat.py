"""Shared chat implementation for OpenAI-compatible adapters.

Used by OllamaAdapter, OpenRouterAdapter, GroqAdapter, and VLLMAdapter.
All four providers accept the same request shape and return the same response shape,
differing only in base URL and authentication header.
"""
from __future__ import annotations

import time
from typing import Any

import openai

from travel_agent.llm._tool_translation import parse_openai_tool_calls, to_openai_tools
from travel_agent.llm.base import LLMError, LLMResponse, Message, ToolDefinition


async def openai_compat_chat(
    client: openai.AsyncOpenAI,
    messages: list[Message],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    system: str | None,
    tools: list[ToolDefinition] | None,
) -> LLMResponse:
    """Execute a chat completion against any OpenAI-compatible endpoint."""
    api_messages: list[dict[str, Any]] = []
    if system:
        api_messages.append({"role": "system", "content": system})
    api_messages.extend({"role": m.role, "content": m.content} for m in messages)

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        create_kwargs["tools"] = to_openai_tools(tools)
        create_kwargs["tool_choice"] = "auto"

    start = time.monotonic()
    try:
        response = await client.chat.completions.create(**create_kwargs)
    except openai.APIError as exc:
        raise LLMError(str(exc)) from exc
    latency_ms = (time.monotonic() - start) * 1000

    choice = response.choices[0]
    content_text = choice.message.content or ""
    raw_tool_calls = list(choice.message.tool_calls) if choice.message.tool_calls else None
    tool_calls = parse_openai_tool_calls(raw_tool_calls)
    usage = response.usage

    return LLMResponse(
        content=content_text,
        model=response.model,
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
        latency_ms=latency_ms,
        tool_calls=tool_calls,
    )
