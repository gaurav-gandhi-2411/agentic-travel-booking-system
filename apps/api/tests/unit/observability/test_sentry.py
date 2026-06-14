"""Unit tests for the Sentry observability wrapper.

Verifies that init_sentry() is a complete no-op when SENTRY_DSN is
unset or empty, and that it calls sentry_sdk.init with the correct
arguments when a DSN is present.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


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
