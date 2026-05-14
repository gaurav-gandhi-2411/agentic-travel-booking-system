"""Global test configuration.

Ensures tests always run in synthetic mode regardless of any .env file that
load_dotenv() may have picked up from the project root.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force synthetic/local mode for every test; wipe credentials."""
    monkeypatch.setenv("APP_MODE", "synthetic")
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "local")
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AVIASALES_API_KEY", raising=False)
    monkeypatch.delenv("AVIASALES_PARTNER_ID", raising=False)
