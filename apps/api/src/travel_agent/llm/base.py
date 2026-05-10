from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


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
        **kwargs: Any,
    ) -> LLMResponse: ...
