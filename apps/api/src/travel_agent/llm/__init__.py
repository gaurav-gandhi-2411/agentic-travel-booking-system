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
from travel_agent.llm.fallback import AllProvidersExhaustedError, FallbackHop, FallbackLLMClient
from travel_agent.llm.groq import GroqAdapter
from travel_agent.llm.nvidia import NIMAdapter
from travel_agent.llm.ollama import OllamaAdapter
from travel_agent.llm.openrouter import OpenRouterAdapter
from travel_agent.llm.routing import (
    get_fallback_chain_for_profile,
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


def get_llm_client_for_provider(provider: str) -> LLMClient:
    """Instantiate a client for a named provider without an agent or profile lookup.

    Used for flat profiles (NIM-style) where the model key is stored directly
    on the profile rather than under per-agent keys.
    """
    return _build_client_for_provider(provider)


def get_llm_client_and_model(
    agent: str, profile_name: str, *, use_fallback: bool = True
) -> tuple[LLMClient, str]:
    """Return (client, model_id) for *agent* under an explicit named profile.

    Used for per-request profile overrides (X-LLM-Profile header). Does not
    read LLM_ROUTING_PROFILE from the environment.

    When the profile declares a fallback_chain for *agent* (llm_routing.yaml)
    and use_fallback is True (the default), the returned client is a
    FallbackLLMClient that tries each configured hop in order on a retryable
    error (429/timeout/5xx). Pass use_fallback=False to force the bare primary
    client — used by the Wave 2 eval runner's authoritative baseline run, which
    must stay single-model-clean (see evals/wave2/README.md).
    """
    model = get_model_for_agent_in_profile(agent, profile_name)
    provider = get_provider_for_profile(profile_name)
    primary_client = _build_client_for_provider(provider)

    if not use_fallback:
        return primary_client, model

    chain = get_fallback_chain_for_profile(profile_name).get(agent)
    if not chain:
        return primary_client, model

    fallbacks = [
        FallbackHop(
            provider=hop["provider"],
            model=hop["model"],
            client=_build_client_for_provider(hop["provider"]),
        )
        for hop in chain
    ]
    return FallbackLLMClient(primary_client, provider, fallbacks), model


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
    "AllProvidersExhaustedError",
    "FallbackHop",
    "FallbackLLMClient",
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "Message",
    "ToolCall",
    "ToolDefinition",
    "get_llm_client",
    "get_llm_client_and_model",
    "get_llm_client_for_provider",
]
