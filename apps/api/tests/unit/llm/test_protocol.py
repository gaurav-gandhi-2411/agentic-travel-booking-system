"""Verify all LLM adapters structurally implement the LLMClient Protocol."""
import pytest

from travel_agent.llm.anthropic import AnthropicAdapter
from travel_agent.llm.base import LLMClient
from travel_agent.llm.groq import GroqAdapter
from travel_agent.llm.ollama import OllamaAdapter
from travel_agent.llm.openrouter import OpenRouterAdapter
from travel_agent.llm.vllm import VLLMAdapter


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
