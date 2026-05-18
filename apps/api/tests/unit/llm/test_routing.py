"""Verify routing config validity and profile-switching behaviour."""

from pathlib import Path

import pytest
import yaml

from travel_agent.llm.routing import (
    AGENT_KEYS,
    _load_yaml,
    get_active_profile_name,
    get_model_for_agent,
    get_model_for_agent_in_profile,
    get_profile_by_name,
    get_provider,
    get_provider_for_profile,
    load_routing_config,
)

# demo-qwen demoted 2026-05-16: OpenRouter removed qwen-2.5-72b-instruct:free.
# Agent profiles: full per-agent model keys. Flat profiles: single model + provider.
_AGENT_PROFILES = {"local", "free", "prod", "eval", "demo", "demo-haiku", "demo-llama"}
# Flat profiles: model+provider only (no per-agent keys). Same skip logic as judge profiles.
_FLAT_PROVIDER_PROFILES = {"demo-deepseek-v4", "demo-qwen3-5"}
_JUDGE_PROFILES = {"eval-judge-qwen3-32b", "eval-judge-sonnet"}
_EXPECTED_PROFILES = _AGENT_PROFILES | _FLAT_PROVIDER_PROFILES | _JUDGE_PROFILES


@pytest.fixture(autouse=True)
def _clear_yaml_cache() -> None:
    """Clear the lru_cache on _load_yaml between tests so env-var overrides work."""
    _load_yaml.cache_clear()


def test_all_profiles_present() -> None:
    config = load_routing_config()
    assert set(config.keys()) == _EXPECTED_PROFILES


def test_all_agents_in_all_profiles() -> None:
    config = load_routing_config()
    for profile_name, profile in config.items():
        if profile_name in _JUDGE_PROFILES | _FLAT_PROVIDER_PROFILES:
            continue  # flat profiles have no per-agent keys
        missing = AGENT_KEYS - set(profile.keys())
        assert not missing, f"Profile {profile_name!r} missing agents: {missing}"


def test_all_profiles_have_provider() -> None:
    config = load_routing_config()
    for profile_name, profile in config.items():
        assert "provider" in profile, f"Profile {profile_name!r} missing 'provider' key"


def test_default_profile_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ROUTING_PROFILE", raising=False)
    assert get_active_profile_name() == "local"


def test_profile_switching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "free")
    assert get_active_profile_name() == "free"


def test_get_model_for_agent_returns_string() -> None:
    model = get_model_for_agent("planner")
    assert isinstance(model, str)
    assert model


def test_get_model_for_all_agents() -> None:
    for agent in AGENT_KEYS:
        model = get_model_for_agent(agent)
        assert isinstance(model, str), f"Agent {agent!r} returned non-string model"
        assert model, f"Agent {agent!r} returned empty model string"


def test_unknown_agent_raises() -> None:
    with pytest.raises(ValueError, match="not found in routing profile"):
        get_model_for_agent("nonexistent_agent")


def test_unknown_profile_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "nonexistent")
    with pytest.raises(ValueError, match="Unknown LLM_ROUTING_PROFILE"):
        get_provider()


def test_local_provider_is_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ROUTING_PROFILE", raising=False)
    assert get_provider() == "ollama"


def test_eval_provider_is_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "eval")
    assert get_provider() == "anthropic"


def test_prod_provider_is_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "prod")
    assert get_provider() == "anthropic"


def test_demo_profile_uses_haiku() -> None:
    # demo profile is Haiku-only for latency and cost
    config = load_routing_config()
    haiku = "claude-haiku-4-5-20251001"
    for agent in AGENT_KEYS:
        assert config["demo"][agent] == haiku, (
            f"Agent {agent!r}: expected Haiku in demo profile, got {config['demo'][agent]!r}"
        )


def test_demo_haiku_profile_matches_demo() -> None:
    config = load_routing_config()
    haiku = "claude-haiku-4-5-20251001"
    for agent in AGENT_KEYS:
        assert config["demo-haiku"][agent] == haiku, (
            f"Agent {agent!r}: expected Haiku in demo-haiku profile"
        )
    assert config["demo-haiku"]["provider"] == "anthropic"


def test_demo_llama_profile_uses_groq() -> None:
    config = load_routing_config()
    assert config["demo-llama"]["provider"] == "groq"
    assert "llama" in config["demo-llama"]["planner"]
    assert "llama" in config["demo-llama"]["optimizer"]


def test_demo_deepseek_v4_profile_uses_nvidia_flash() -> None:
    config = load_routing_config()
    profile = config["demo-deepseek-v4"]
    assert profile["provider"] == "nvidia"
    assert profile["model"] == "deepseek-ai/deepseek-v4-flash"
    # Flat profile: no per-agent keys
    from travel_agent.llm.routing import AGENT_KEYS

    assert not AGENT_KEYS.intersection(profile.keys()), (
        "demo-deepseek-v4 must be flat (model+provider only), not agent-routed"
    )


def test_demo_qwen3_5_profile_uses_nvidia_qwen35() -> None:
    config = load_routing_config()
    profile = config["demo-qwen3-5"]
    assert profile["provider"] == "nvidia"
    assert profile["model"] == "qwen/qwen3.5-397b-a17b"
    assert profile["temperature"] == 0.0
    assert profile["max_tokens"] == 1024
    # Flat profile: no per-agent keys
    assert not AGENT_KEYS.intersection(profile.keys()), (
        "demo-qwen3-5 must be flat (model+provider only), not agent-routed"
    )


def test_demo_qwen_profile_absent() -> None:
    # demo-qwen demoted 2026-05-16: profile commented out in llm_routing.yaml.
    config = load_routing_config()
    assert "demo-qwen" not in config


def test_get_profile_by_name() -> None:
    profile = get_profile_by_name("demo-llama")
    assert profile["provider"] == "groq"


def test_get_profile_by_name_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown routing profile"):
        get_profile_by_name("nonexistent")


def test_get_model_for_agent_in_profile() -> None:
    model = get_model_for_agent_in_profile("planner", "demo-llama")
    assert "llama" in model.lower()


def test_get_provider_for_profile() -> None:
    assert get_provider_for_profile("demo-haiku") == "anthropic"
    assert get_provider_for_profile("demo-llama") == "groq"


def test_config_path_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """LLM_ROUTING_CONFIG_PATH env var overrides the default config file path."""
    minimal_config: dict[str, object] = {
        "profiles": {
            "local": {
                "planner": "test-model",
                "flight_hunter": "test-model",
                "hotel_hunter": "test-model",
                "optimizer": "test-model",
                "booking": "test-model",
                "conversation": "test-model",
                "provider": "ollama",
            }
        }
    }
    config_file = tmp_path / "test_routing.yaml"
    config_file.write_text(yaml.dump(minimal_config))
    monkeypatch.setenv("LLM_ROUTING_CONFIG_PATH", str(config_file))
    _load_yaml.cache_clear()
    config = load_routing_config()
    assert "local" in config
