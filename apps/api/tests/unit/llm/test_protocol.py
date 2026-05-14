"""Verify all LLM adapters structurally implement the LLMClient Protocol.

Also covers the base dataclasses (Message, ToolDefinition, ToolCall, LLMResponse)
to keep coverage above the fail_under threshold.
"""
import pytest

from travel_agent.llm.anthropic import AnthropicAdapter
from travel_agent.llm.base import (
    LLMClient,
    LLMError,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
)
from travel_agent.llm.groq import GroqAdapter
from travel_agent.llm.ollama import OllamaAdapter
from travel_agent.llm.openrouter import OpenRouterAdapter
from travel_agent.llm.vllm import VLLMAdapter

# ── Protocol conformance ──────────────────────────────────────────────────────


def test_ollama_implements_protocol() -> None:
    assert isinstance(OllamaAdapter(), LLMClient)


def test_openrouter_implements_protocol() -> None:
    assert isinstance(OpenRouterAdapter(), LLMClient)


def test_groq_implements_protocol() -> None:
    assert isinstance(GroqAdapter(), LLMClient)


def test_vllm_implements_protocol() -> None:
    assert isinstance(VLLMAdapter(), LLMClient)


def test_anthropic_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicAdapter()


def test_anthropic_implements_protocol_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    assert isinstance(AnthropicAdapter(), LLMClient)


# ── Dataclass construction ────────────────────────────────────────────────────


def test_message_construction() -> None:
    msg = Message(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"


def test_tool_definition_construction() -> None:
    td = ToolDefinition(
        name="search_flights",
        description="Search for available flights",
        input_schema={"type": "object", "properties": {"origin": {"type": "string"}}},
    )
    assert td.name == "search_flights"
    assert "properties" in td.input_schema


def test_tool_call_construction() -> None:
    tc = ToolCall(name="search_flights", input={"origin": "BOM"}, id="call_123")
    assert tc.name == "search_flights"
    assert tc.input["origin"] == "BOM"
    assert tc.id == "call_123"


def test_tool_call_id_defaults_empty() -> None:
    tc = ToolCall(name="fn", input={})
    assert tc.id == ""


def test_llm_response_construction() -> None:
    resp = LLMResponse(
        content="Here are your results",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        latency_ms=350.0,
    )
    assert resp.tool_calls == []
    assert resp.content == "Here are your results"


def test_llm_response_with_tool_calls() -> None:
    tc = ToolCall(name="fn", input={"key": "val"})
    resp = LLMResponse(
        content="",
        model="claude-haiku-4-5-20251001",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100.0,
        tool_calls=[tc],
    )
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "fn"


def test_llm_error_is_exception() -> None:
    err = LLMError("provider returned 429")
    assert isinstance(err, Exception)
    assert "429" in str(err)
