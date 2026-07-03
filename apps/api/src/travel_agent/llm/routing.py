"""LLM routing — loads per-agent model and provider config from llm_routing.yaml.

The public API (get_active_profile_name, get_active_profile, get_model_for_agent,
get_provider, load_routing_config) is stable; only the backing store changed from
a hardcoded dict (Phase 0) to YAML loading (Quick-Win Q10, 2026-05-14).

Override the config file path with the LLM_ROUTING_CONFIG_PATH env var for testing
or non-standard deployments.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml

_DEFAULT_PROFILE = "local"

# Path relative to this file: apps/api/src/travel_agent/llm/ → apps/api/config/
_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "llm_routing.yaml"

AGENT_KEYS: frozenset[str] = frozenset(
    {"planner", "flight_hunter", "hotel_hunter", "optimizer", "booking", "conversation"}
)


def _config_path() -> Path:
    env = os.environ.get("LLM_ROUTING_CONFIG_PATH")
    return Path(env) if env else _DEFAULT_CONFIG_PATH


@lru_cache(maxsize=1)
def _load_yaml(path: str) -> dict[str, Any]:
    """Load and cache the YAML config. Cached by path string for determinism."""
    p = Path(path)
    try:
        with p.open() as f:
            return cast("dict[str, Any]", yaml.safe_load(f))
    except FileNotFoundError:
        msg = (
            f"LLM routing config not found at {p}. "
            "Set LLM_ROUTING_CONFIG_PATH env var to override the path."
        )
        raise FileNotFoundError(msg) from None


def load_routing_config() -> dict[str, dict[str, str]]:
    """Return the full routing config as a profile-name → settings dict.

    Profiles can contain non-string values (e.g. fallback_models is a nested
    dict). Only AGENT_KEYS and 'provider'/'base_url' are read by the lookup
    functions; extra keys are silently ignored.
    """
    raw = _load_yaml(str(_config_path()))
    return cast("dict[str, dict[str, str]]", raw["profiles"])


def get_active_profile_name() -> str:
    return os.environ.get("LLM_ROUTING_PROFILE", _DEFAULT_PROFILE).strip()


def get_active_profile() -> dict[str, str]:
    profile_name = get_active_profile_name()
    config = load_routing_config()
    if profile_name not in config:
        msg = (
            f"Unknown LLM_ROUTING_PROFILE={profile_name!r}. Valid profiles: {sorted(config.keys())}"
        )
        raise ValueError(msg)
    return config[profile_name]


def get_model_for_agent(agent: str) -> str:
    profile = get_active_profile()
    if agent not in AGENT_KEYS or agent not in profile:
        profile_name = get_active_profile_name()
        msg = f"Agent {agent!r} not found in routing profile {profile_name!r}."
        raise ValueError(msg)
    return profile[agent]


def get_provider() -> str:
    return get_active_profile()["provider"]


def get_profile_by_name(name: str) -> dict[str, str]:
    """Return a routing profile by explicit name, bypassing env-var resolution."""
    config = load_routing_config()
    if name not in config:
        msg = f"Unknown routing profile {name!r}. Valid profiles: {sorted(config.keys())}"
        raise ValueError(msg)
    return config[name]


def get_model_for_agent_in_profile(agent: str, profile_name: str) -> str:
    """Return the model ID for *agent* in a specific named profile."""
    profile = get_profile_by_name(profile_name)
    if agent not in AGENT_KEYS or agent not in profile:
        msg = f"Agent {agent!r} not found in routing profile {profile_name!r}."
        raise ValueError(msg)
    return profile[agent]


def get_provider_for_profile(profile_name: str) -> str:
    return get_profile_by_name(profile_name)["provider"]


def get_fallback_chain_for_profile(profile_name: str) -> dict[str, list[dict[str, str]]]:
    """Return the fallback_chain config for *profile_name*, or {} if absent.

    Shape: ``{agent_key: [{"provider": str, "model": str}, ...]}``. Only agents
    explicitly listed get a fallback client — an agent key absent from
    fallback_chain (e.g. "conversation" on demo-llama) uses just its primary
    provider/model, unchanged. See llm_routing.yaml for the current chains.
    """
    profile = get_profile_by_name(profile_name)
    raw_chain = profile.get("fallback_chain")
    if not raw_chain:
        return {}
    return cast("dict[str, list[dict[str, str]]]", raw_chain)
