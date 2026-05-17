"""Unit tests for scorer cost-display formatting."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "evals"))

from optimizer.scorer import _format_provider_spend


def test_paid_provider_no_calls() -> None:
    assert _format_provider_spend("Anthropic spend this run", 0.0, 0, free_tier=False) == (
        "  Anthropic spend this run: $0 (no calls)"
    )


def test_paid_provider_calls_untracked() -> None:
    assert _format_provider_spend("Anthropic spend this run", 0.0, 72, free_tier=False) == (
        "  Anthropic spend this run: not tracked in this run (72 calls)"
    )


def test_paid_provider_calls_with_spend() -> None:
    line = _format_provider_spend("Anthropic spend this run", 0.10513, 72, free_tier=False)
    assert line == "  !! Anthropic spend this run: $0.10513 (72 calls)"


def test_free_tier_no_calls() -> None:
    assert _format_provider_spend("Groq spend this run", 0.0, 0, free_tier=True) == (
        "  Groq spend this run: $0 (no calls)"
    )


def test_free_tier_with_calls() -> None:
    assert _format_provider_spend("Groq spend this run", 0.0, 63, free_tier=True) == (
        "  Groq spend this run: $0 (63 calls, free tier)"
    )
