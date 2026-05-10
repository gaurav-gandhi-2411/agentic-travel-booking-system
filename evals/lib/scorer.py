"""Metric aggregation for eval runs.

Computes exact-match, field-accuracy, and preference win-rate metrics
from RunResult lists. Compares against baseline.json for regression detection.

Full implementation target: Phase 3.5.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evals.lib.runner import RunResult


@dataclass
class ScoreReport:
    agent: str
    mode: str
    exact_match: float
    preference_win_rate: float
    sample_size: int
    regression_detected: bool


def score_results(
    results: list[RunResult],
    *,
    agent: str,
    mode: str,
    baseline_path: Path | None = None,
) -> ScoreReport:
    """Aggregate *results* into a ScoreReport, optionally checking against *baseline_path*.

    Regression is flagged when any metric drops >2% vs baseline.
    """
    msg = f"scorer.score_results — implemented in Phase 3.5 (agent={agent!r}, mode={mode!r})"
    raise NotImplementedError(msg)
