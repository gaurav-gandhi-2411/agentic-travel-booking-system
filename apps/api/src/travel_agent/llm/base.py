from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str


@dataclass
class ToolDefinition:
    """Describes a tool (function) the LLM can call.

    Maps to Anthropic's tool schema: name, description, input_schema (JSON Schema).
    Other providers (OpenRouter, Groq) use the same shape via OpenAI-compatible API.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    """A tool invocation returned by the LLM in its response."""

    name: str
    input: dict[str, Any]
    id: str = ""  # provider-assigned call ID (present on Anthropic responses)


@dataclass
class LLMResponse:
    content: str  # text content; empty string when the response is tool-calls-only
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Prompt-caching token counts (Anthropic only; 0 for other providers)
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class LLMError(Exception):
    pass


@runtime_checkable
class LLMClient(Protocol):
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
    ) -> LLMResponse: ...
