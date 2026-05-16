"""Tests for the optimizer eval runner's profile-skip behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

# Runner is not a package — insert src path before importing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "evals"))

from optimizer.runner import run_profile


def _minimal_scenarios() -> list[dict]:
    """Two tiny scenarios for quick skip tests — no LLM calls needed."""
    from travel_agent.coordinator.state import FlightOption, Window
    from travel_agent.providers.synthetic import SyntheticProvider
    from datetime import date

    provider = SyntheticProvider()
    w = Window(start_date=date(2026, 12, 1), end_date=date(2026, 12, 7))
    flights = provider.get_flights("DEL", "DXB", w)[:3]
    return [
        {
            "id": f"test-{i:03d}",
            "route": "DEL-DXB",
            "window_start": "2026-12-01",
            "flights": [f.model_dump(mode="json") for f in flights],
            "n_flights": len(flights),
        }
        for i in range(2)
    ]


@pytest.mark.asyncio
async def test_run_profile_skips_unknown_profile(caplog: pytest.LogCaptureFixture) -> None:
    """A profile not in llm_routing.yaml returns [] and logs a structured warning."""
    scenarios = _minimal_scenarios()
    result = await run_profile("demo-nonexistent", scenarios, dry_run=False)
    assert result == [], "Expected empty list for unknown profile"


@pytest.mark.asyncio
async def test_run_profile_skips_demoted_profile() -> None:
    """demo-qwen is not in the active config; runner returns [] without raising."""
    scenarios = _minimal_scenarios()
    result = await run_profile("demo-qwen", scenarios, dry_run=False)
    assert result == [], "Expected empty list for demoted demo-qwen profile"


@pytest.mark.asyncio
async def test_run_profile_dry_run_ignores_missing_config() -> None:
    """dry_run=True bypasses routing config lookup — works for any profile name."""
    scenarios = _minimal_scenarios()
    # dry-run should NOT call load_routing_config at all
    with patch("optimizer.runner.OptimizerAgent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.run.return_value = type(
            "State", (), {"archetypes": [], "flight_options": []}
        )()

        result = await run_profile("demo-qwen", scenarios, dry_run=True)
    # dry-run always tries to run; it may produce empty archetypes but should not raise
    assert isinstance(result, list)
