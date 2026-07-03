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


_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_THRESHOLD = 500


def _default_retryable(status_code: int | None) -> bool:
    """429/5xx/unknown-connection-error (no status code) are transient; other
    4xx (400, 401, ...) mean the request itself was bad and won't succeed on
    a different provider either."""
    if status_code is None:
        return True
    if status_code == _HTTP_TOO_MANY_REQUESTS:
        return True
    return status_code >= _HTTP_SERVER_ERROR_THRESHOLD


class LLMError(Exception):
    """Raised by an LLMClient adapter on any request failure.

    status_code carries the HTTP status when known (None for connection/timeout
    errors that never reached the provider). retryable distinguishes transient
    failures (429/5xx/timeout) — safe to retry on a different provider — from
    ones our own request caused (400 and other 4xx), which would fail
    identically everywhere. See travel_agent.llm.fallback.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = _default_retryable(status_code) if retryable is None else retryable


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
