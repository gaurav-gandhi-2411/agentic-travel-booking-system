"""get_llm_client_and_model — fallback-chain wiring per routing profile/agent."""

from __future__ import annotations

import pytest

from travel_agent.llm import FallbackLLMClient, get_llm_client_and_model
from travel_agent.llm.groq import GroqAdapter
from travel_agent.llm.routing import _load_yaml


@pytest.fixture(autouse=True)
def _clear_yaml_cache() -> None:
    _load_yaml.cache_clear()


@pytest.fixture(autouse=True)
def _provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")


def test_planner_on_demo_llama_gets_fallback_client() -> None:
    client, model = get_llm_client_and_model("planner", "demo-llama")
    assert isinstance(client, FallbackLLMClient)
    assert model == "llama-3.3-70b-versatile"


def test_optimizer_on_demo_llama_gets_fallback_client() -> None:
    client, _model = get_llm_client_and_model("optimizer", "demo-llama")
    assert isinstance(client, FallbackLLMClient)


def test_conversation_on_demo_llama_gets_fallback_client() -> None:
    """conversation_manager's tool schema (ConversationManagerOutput's
    exactly-one-of-args invariant) was validated separately against Gemma-4-31B
    and passed -- it shares the same fallback hop as planner/optimizer."""
    client, model = get_llm_client_and_model("conversation", "demo-llama")
    assert isinstance(client, FallbackLLMClient)
    assert model == "llama-3.3-70b-versatile"


def test_use_fallback_false_forces_plain_client() -> None:
    client, model = get_llm_client_and_model("planner", "demo-llama", use_fallback=False)
    assert not isinstance(client, FallbackLLMClient)
    assert isinstance(client, GroqAdapter)
    assert model == "llama-3.3-70b-versatile"


def test_conversation_use_fallback_false_forces_plain_client() -> None:
    client, _model = get_llm_client_and_model("conversation", "demo-llama", use_fallback=False)
    assert not isinstance(client, FallbackLLMClient)
    assert isinstance(client, GroqAdapter)


def test_profile_without_fallback_chain_stays_plain_client() -> None:
    """The 'free' profile has no fallback_chain configured -- unaffected by this feature."""
    client, _model = get_llm_client_and_model("planner", "free")
    assert not isinstance(client, FallbackLLMClient)
