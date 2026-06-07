"""Unit tests for AVIASALES_LIVE and AFFILIATE_DEEPLINKS feature flags.

Phase 3.1: These flags gate live Aviasales adapter injection and affiliate
deeplink emission respectively.

AVIASALES_LIVE controls _get_adapter() in coordinator/streaming.py:
  - truthy values: "true", "1" (case-insensitive); anything else returns None
  - RuntimeError from AviasalesAdapter (missing key) is caught — returns None

AFFILIATE_DEEPLINKS controls partner_marker in _build_agents() in api/routes/search.py:
  - default: "true" (on); suppressed by "false" or "0"
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from travel_agent.api.routes.search import _build_agents
from travel_agent.coordinator.streaming import _get_adapter

# ---------------------------------------------------------------------------
# _get_adapter() — AVIASALES_LIVE flag
# ---------------------------------------------------------------------------


def test_get_adapter_returns_none_when_flag_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when AVIASALES_LIVE is not set, regardless of key presence."""
    monkeypatch.delenv("AVIASALES_LIVE", raising=False)
    monkeypatch.setenv("AVIASALES_API_KEY", "test-key")

    assert _get_adapter() is None


def test_get_adapter_returns_none_when_flag_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when AVIASALES_LIVE is explicitly "false"."""
    monkeypatch.setenv("AVIASALES_LIVE", "false")
    monkeypatch.setenv("AVIASALES_API_KEY", "test-key")

    assert _get_adapter() is None


def test_get_adapter_returns_adapter_when_flag_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns an AviasalesAdapter instance when AVIASALES_LIVE="true" and key is set."""
    monkeypatch.setenv("AVIASALES_LIVE", "true")
    monkeypatch.setenv("AVIASALES_API_KEY", "test-key")

    result = _get_adapter()

    assert result is not None


def test_get_adapter_returns_adapter_when_flag_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns an AviasalesAdapter instance when AVIASALES_LIVE="1" and key is set."""
    monkeypatch.setenv("AVIASALES_LIVE", "1")
    monkeypatch.setenv("AVIASALES_API_KEY", "test-key")

    result = _get_adapter()

    assert result is not None


def test_get_adapter_returns_none_when_live_true_but_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns None gracefully when AVIASALES_LIVE=true but AVIASALES_API_KEY is absent.

    AviasalesAdapter.__init__ raises RuntimeError on a missing key; _get_adapter
    catches it and falls back to None rather than propagating.
    """
    monkeypatch.setenv("AVIASALES_LIVE", "true")
    monkeypatch.delenv("AVIASALES_API_KEY", raising=False)

    assert _get_adapter() is None


def test_get_adapter_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """AVIASALES_LIVE comparison is case-insensitive; "TRUE" activates the flag."""
    monkeypatch.setenv("AVIASALES_LIVE", "TRUE")
    monkeypatch.setenv("AVIASALES_API_KEY", "test-key")

    result = _get_adapter()

    assert result is not None


# ---------------------------------------------------------------------------
# _build_agents() — AFFILIATE_DEEPLINKS flag
# ---------------------------------------------------------------------------

_MOCK_LLM_TARGET = "travel_agent.api.routes.search.get_llm_client_and_model"


def test_build_agents_affiliate_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """partner_marker is set from AVIASALES_PARTNER_ID when AFFILIATE_DEEPLINKS is absent.

    Default semantics: flag absent → treated as "true" → affiliate links on.
    """
    monkeypatch.setenv("AVIASALES_PARTNER_ID", "my-marker")
    monkeypatch.delenv("AFFILIATE_DEEPLINKS", raising=False)

    with patch(_MOCK_LLM_TARGET, return_value=(MagicMock(), "test-model")):
        _, optimizer = _build_agents("demo-llama")

    assert optimizer._partner_marker == "my-marker"


def test_build_agents_affiliate_off_suppresses_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """partner_marker is empty when affiliate_enabled=False (per-tenant config).

    Phase 3.2-A Step 4: affiliate gating moved from AFFILIATE_DEEPLINKS env var
    to the per-tenant affiliate_enabled flag passed as a parameter.
    """
    monkeypatch.setenv("AVIASALES_PARTNER_ID", "my-marker")

    with patch(_MOCK_LLM_TARGET, return_value=(MagicMock(), "test-model")):
        _, optimizer = _build_agents("demo-llama", affiliate_enabled=False)

    assert optimizer._partner_marker == ""


def test_build_agents_affiliate_off_with_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """partner_marker is empty when affiliate_enabled=False even with AVIASALES_PARTNER_ID set.

    Phase 3.2-A Step 4: the affiliate_enabled parameter (from tenant config) is
    the sole gating mechanism — env-var AFFILIATE_DEEPLINKS is no longer consulted.
    """
    monkeypatch.setenv("AVIASALES_PARTNER_ID", "my-marker")

    with patch(_MOCK_LLM_TARGET, return_value=(MagicMock(), "test-model")):
        _, optimizer = _build_agents("demo-llama", affiliate_enabled=False)

    assert optimizer._partner_marker == ""


def test_build_agents_affiliate_on_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """partner_marker is set when AFFILIATE_DEEPLINKS is explicitly "true"."""
    monkeypatch.setenv("AVIASALES_PARTNER_ID", "my-marker")
    monkeypatch.setenv("AFFILIATE_DEEPLINKS", "true")

    with patch(_MOCK_LLM_TARGET, return_value=(MagicMock(), "test-model")):
        _, optimizer = _build_agents("demo-llama")

    assert optimizer._partner_marker == "my-marker"


def test_build_agents_affiliate_on_no_partner_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """partner_marker is empty when affiliate is on but AVIASALES_PARTNER_ID is absent.

    Even with affiliate links enabled, no marker can be injected if the partner
    ID env var is not set.
    """
    monkeypatch.delenv("AVIASALES_PARTNER_ID", raising=False)
    monkeypatch.setenv("AFFILIATE_DEEPLINKS", "true")

    with patch(_MOCK_LLM_TARGET, return_value=(MagicMock(), "test-model")):
        _, optimizer = _build_agents("demo-llama")

    assert optimizer._partner_marker == ""
