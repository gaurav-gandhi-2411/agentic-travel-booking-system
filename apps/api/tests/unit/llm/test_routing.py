"""Verify routing config validity and profile-switching behaviour."""
import pytest

from travel_agent.llm.routing import (
    AGENT_KEYS,
    get_active_profile_name,
    get_model_for_agent,
    get_provider,
    load_routing_config,
)

_EXPECTED_PROFILES = {"local", "free", "eval"}


def test_all_profiles_present() -> None:
    config = load_routing_config()
    assert _EXPECTED_PROFILES == set(config.keys())


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
