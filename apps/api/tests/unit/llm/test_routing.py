"""Verify routing config validity and profile-switching behaviour."""

from pathlib import Path

import pytest
import yaml

from travel_agent.llm.routing import (
    AGENT_KEYS,
    _load_yaml,
    get_active_profile_name,
    get_model_for_agent,
    get_provider,
    load_routing_config,
)

_EXPECTED_PROFILES = {"local", "free", "prod", "eval", "demo"}


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
