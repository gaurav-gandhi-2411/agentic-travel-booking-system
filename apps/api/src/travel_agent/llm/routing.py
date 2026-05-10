"""LLM routing — stub implementation backed by a hardcoded table.

Phase 2.5 replaces this with YAML-backed routing loaded from
apps/api/config/llm_routing.yaml. The public API (get_active_profile_name,
get_active_profile, get_model_for_agent, get_provider, load_routing_config)
is stable; only the backing store changes.
"""
from __future__ import annotations

import os

_DEFAULT_PROFILE = "local"

# Routing table mirrors apps/api/config/llm_routing.yaml.
# Phase 2.5 will load this from the YAML file instead.
_ROUTING: dict[str, dict[str, str]] = {
    "local": {
        "planner": "qwen2.5:7b",
        "flight_hunter": "qwen2.5:7b",
        "hotel_hunter": "qwen2.5:7b",
        "optimizer": "qwen2.5:14b",
        "booking": "qwen2.5:7b",
        "conversation": "qwen2.5:14b",
        "provider": "ollama",
        "base_url": "http://localhost:11434",
    },
    "free": {
        "planner": "qwen/qwen-2.5-72b-instruct:free",
        "flight_hunter": "meta-llama/llama-3.3-70b-instruct:free",
        "hotel_hunter": "meta-llama/llama-3.3-70b-instruct:free",
        "optimizer": "qwen/qwen-2.5-72b-instruct:free",
        "booking": "meta-llama/llama-3.3-70b-instruct:free",
        "conversation": "qwen/qwen-2.5-72b-instruct:free",
        "provider": "openrouter",
    },
    "eval": {
        "planner": "claude-sonnet-4-6",
        "flight_hunter": "claude-haiku-4-5-20251001",
        "hotel_hunter": "claude-haiku-4-5-20251001",
        "optimizer": "claude-sonnet-4-6",
        "booking": "claude-haiku-4-5-20251001",
        "conversation": "claude-sonnet-4-6",
        "provider": "anthropic",
    },
}

AGENT_KEYS: frozenset[str] = frozenset(
    {"planner", "flight_hunter", "hotel_hunter", "optimizer", "booking", "conversation"}
)


def load_routing_config() -> dict[str, dict[str, str]]:
    """Return the routing config table.

    Phase 2.5: replace with YAML loading from apps/api/config/llm_routing.yaml.
    """
    return _ROUTING


def get_active_profile_name() -> str:
    return os.environ.get("LLM_ROUTING_PROFILE", _DEFAULT_PROFILE)


def get_active_profile() -> dict[str, str]:
    profile_name = get_active_profile_name()
    config = load_routing_config()
    if profile_name not in config:
        msg = (
            f"Unknown LLM_ROUTING_PROFILE={profile_name!r}. "
            f"Valid profiles: {sorted(config.keys())}"
        )
        raise ValueError(msg)
    return config[profile_name]


def get_model_for_agent(agent: str) -> str:
    profile = get_active_profile()
    if agent not in profile:
        profile_name = get_active_profile_name()
        msg = f"Agent {agent!r} not found in routing profile {profile_name!r}."
        raise ValueError(msg)
    return profile[agent]


def get_provider() -> str:
    return get_active_profile()["provider"]
