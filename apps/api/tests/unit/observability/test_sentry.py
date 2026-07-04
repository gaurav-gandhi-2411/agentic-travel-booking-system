"""Unit tests for the Sentry observability wrapper.

Verifies that init_sentry() is a complete no-op when SENTRY_DSN is
unset or empty, and that it calls sentry_sdk.init with the correct
arguments when a DSN is present.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from travel_agent.llm.fallback import LLM_FALLBACK_MANAGED_TAG
from travel_agent.observability.sentry import _before_send, _is_recovered_fallback_hop_noise


def test_init_sentry_noop_when_dsn_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_sentry() must not import or call sentry_sdk when SENTRY_DSN is absent."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    from travel_agent.observability.sentry import init_sentry

    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
        mock_init.assert_not_called()


def test_init_sentry_noop_when_dsn_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_sentry() must not call sentry_sdk.init when SENTRY_DSN is empty string."""
    monkeypatch.setenv("SENTRY_DSN", "")

    from travel_agent.observability.sentry import init_sentry

    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
        mock_init.assert_not_called()


def test_init_sentry_noop_when_dsn_whitespace_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_sentry() strips whitespace before checking — '   ' is treated as empty."""
    monkeypatch.setenv("SENTRY_DSN", "   ")

    from travel_agent.observability.sentry import init_sentry

    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
        mock_init.assert_not_called()


def test_init_sentry_calls_sdk_init_when_dsn_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """init_sentry() calls sentry_sdk.init with DSN and FastAPI/Starlette integrations."""
    test_dsn = "https://abc123@o0.ingest.sentry.io/0"
    monkeypatch.setenv("SENTRY_DSN", test_dsn)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)

    mock_sdk = MagicMock()
    mock_fastapi_integration = MagicMock()
    mock_starlette_integration = MagicMock()

    with (
        patch.dict(
            "sys.modules",
            {
                "sentry_sdk": mock_sdk,
                "sentry_sdk.integrations.fastapi": MagicMock(
                    FastApiIntegration=mock_fastapi_integration
                ),
                "sentry_sdk.integrations.starlette": MagicMock(
                    StarletteIntegration=mock_starlette_integration
                ),
            },
        ),
    ):
        # Re-import to pick up the patched sys.modules
        import importlib

        import travel_agent.observability.sentry as sentry_mod

        importlib.reload(sentry_mod)
        sentry_mod.init_sentry()

    mock_sdk.init.assert_called_once()
    call_kwargs = mock_sdk.init.call_args.kwargs
    assert call_kwargs["dsn"] == test_dsn
    assert call_kwargs["send_default_pii"] is False
    assert call_kwargs["traces_sample_rate"] == pytest.approx(0.1)


def test_init_sentry_respects_custom_sample_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """SENTRY_TRACES_SAMPLE_RATE env var overrides the default 0.1 sample rate."""
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@o0.ingest.sentry.io/0")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")

    mock_sdk = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "sentry_sdk": mock_sdk,
            "sentry_sdk.integrations.fastapi": MagicMock(FastApiIntegration=MagicMock()),
            "sentry_sdk.integrations.starlette": MagicMock(StarletteIntegration=MagicMock()),
        },
    ):
        import importlib

        import travel_agent.observability.sentry as sentry_mod

        importlib.reload(sentry_mod)
        sentry_mod.init_sentry()

    call_kwargs = mock_sdk.init.call_args.kwargs
    assert call_kwargs["traces_sample_rate"] == pytest.approx(0.5)


# ── _before_send / _is_recovered_fallback_hop_noise (ADR-0028 fix (b)) ────────


def _fallback_event(exc_type: str, *, tagged: bool, tags_shape: str = "dict") -> dict:
    """Build a minimal Sentry event dict shaped like what before_send receives."""
    tags: object
    if not tagged:
        tags = {} if tags_shape == "dict" else []
    elif tags_shape == "dict":
        tags = {LLM_FALLBACK_MANAGED_TAG: "true"}
    else:
        tags = [[LLM_FALLBACK_MANAGED_TAG, "true"]]
    return {
        "tags": tags,
        "exception": {"values": [{"type": exc_type, "value": "boom"}]},
    }


class TestIsRecoveredFallbackHopNoise:
    def test_tagged_retryable_dict_tags_is_noise(self) -> None:
        event = _fallback_event("RateLimitError", tagged=True, tags_shape="dict")
        assert _is_recovered_fallback_hop_noise(event) is True

    def test_tagged_retryable_list_tags_is_noise(self) -> None:
        """Sentry SDK may serialize tags as [[k, v], ...] rather than a dict by
        the time before_send runs -- both shapes must be handled."""
        event = _fallback_event("RateLimitError", tagged=True, tags_shape="list")
        assert _is_recovered_fallback_hop_noise(event) is True

    @pytest.mark.parametrize(
        "exc_type",
        ["RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"],
    )
    def test_all_four_retryable_types_are_noise_when_tagged(self, exc_type: str) -> None:
        event = _fallback_event(exc_type, tagged=True)
        assert _is_recovered_fallback_hop_noise(event) is True

    def test_untagged_call_is_never_noise(self) -> None:
        """A call outside a FallbackLLMClient chain (e.g. demo-gpt-oss-120b, which
        has no fallback configured) carries no tag -- its only Sentry visibility
        is this auto-capture, so it must never be dropped."""
        event = _fallback_event("RateLimitError", tagged=False)
        assert _is_recovered_fallback_hop_noise(event) is False

    def test_non_retryable_exception_is_not_noise_even_when_tagged(self) -> None:
        """A genuine bad-request/auth failure inside a fallback-managed call has
        no other capture mechanism (FallbackLLMClient only calls Sentry itself on
        success or full exhaustion) -- it must still reach Sentry."""
        event = _fallback_event("AuthenticationError", tagged=True)
        assert _is_recovered_fallback_hop_noise(event) is False

    def test_no_exception_values_is_not_noise(self) -> None:
        event = {"tags": {LLM_FALLBACK_MANAGED_TAG: "true"}, "exception": {"values": []}}
        assert _is_recovered_fallback_hop_noise(event) is False

    def test_missing_exception_key_entirely_is_not_noise(self) -> None:
        event = {"tags": {LLM_FALLBACK_MANAGED_TAG: "true"}}
        assert _is_recovered_fallback_hop_noise(event) is False


class TestBeforeSendDropsRecoveredFallbackNoise:
    def test_drops_recovered_fallback_hop_event(self) -> None:
        event = _fallback_event("RateLimitError", tagged=True)
        assert _before_send(event, {}) is None

    def test_full_exhaustion_style_event_untagged_still_passes_through(self) -> None:
        """FallbackLLMClient's own capture_exception(exc) call on full exhaustion
        happens OUTSIDE the tagged scope (see fallback.py) -- it has no tag, so
        before_send must never drop it."""
        event = _fallback_event("AllProvidersExhaustedError", tagged=False)
        result = _before_send(event, {})
        assert result is not None
        assert result["exception"]["values"][0]["type"] == "AllProvidersExhaustedError"

    def test_non_fallback_event_still_gets_scrubbed_normally(self) -> None:
        """Regression check: adding the noise-drop rule must not break the
        existing credential-scrubbing behavior for ordinary events."""
        event = {
            "tags": {},
            "exception": {"values": [{"type": "ValueError", "value": "boom"}]},
            "request": {
                "headers": {"X-API-Key": "secret-value", "Content-Type": "application/json"},
                "url": "https://example.com/search?token=leak-me",
            },
        }
        result = _before_send(event, {})
        assert result is not None
        assert result["request"]["headers"]["X-API-Key"] == "[Scrubbed]"
        assert result["request"]["headers"]["Content-Type"] == "application/json"
        assert "token=leak-me" not in result["request"]["url"]
