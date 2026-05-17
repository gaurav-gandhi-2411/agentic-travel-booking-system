"""LLM provider abstraction — factory and public re-exports.

Usage:
    from travel_agent.llm import get_llm_client

    client = get_llm_client("planner")
    response = await client.chat(messages, model=model)

The active provider and per-agent model are resolved from LLM_ROUTING_PROFILE
(default: "local") via apps/api/config/llm_routing.yaml.
"""

from __future__ import annotations

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
from travel_agent.llm.nvidia import NIMAdapter
from travel_agent.llm.ollama import OllamaAdapter
from travel_agent.llm.openrouter import OpenRouterAdapter
from travel_agent.llm.routing import (
    get_model_for_agent,
    get_model_for_agent_in_profile,
    get_provider,
    get_provider_for_profile,
)
from travel_agent.llm.vllm import VLLMAdapter


def _build_client_for_provider(provider: str) -> LLMClient:
    match provider:
        case "ollama":
            return OllamaAdapter()
        case "openrouter":
            return OpenRouterAdapter()
        case "groq":
            return GroqAdapter()
        case "anthropic":
            return AnthropicAdapter()
        case "nvidia":
            return NIMAdapter()
        case "vllm":
            return VLLMAdapter()
        case _:
            msg = (
                f"Unknown provider {provider!r} in routing config. "
                "Valid providers: ollama, openrouter, groq, anthropic, nvidia, vllm."
            )
            raise ValueError(msg)


def get_llm_client_and_model(agent: str, profile_name: str) -> tuple[LLMClient, str]:
    """Return (client, model_id) for *agent* under an explicit named profile.

    Used for per-request profile overrides (X-LLM-Profile header). Does not
    read LLM_ROUTING_PROFILE from the environment.
    """
    model = get_model_for_agent_in_profile(agent, profile_name)
    provider = get_provider_for_profile(profile_name)
    return _build_client_for_provider(provider), model


def get_llm_client(agent: str) -> LLMClient:
    """Return the LLM adapter for *agent* under the active routing profile.

    Validates the agent name against the current profile. The concrete model
    string is retrieved separately via get_model_for_agent() and passed to
    client.chat() at call time.
    """
    get_model_for_agent(agent)  # validates agent name; model passed to chat() at call site
    provider = get_provider()

    return _build_client_for_provider(provider)


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "Message",
    "ToolCall",
    "ToolDefinition",
    "get_llm_client",
    "get_llm_client_and_model",
]
