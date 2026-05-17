"""Unit tests for optimizer eval gate thresholds."""

from __future__ import annotations

from evals.optimizer.thresholds import (
    THRESHOLD_COHERENCE_MIN,
    THRESHOLD_HIGH_VARIANCE_MAX_PCT,
    THRESHOLD_LABEL_CORRECT,
)


def test_label_correct_threshold_is_one() -> None:
    assert THRESHOLD_LABEL_CORRECT == 1.0


def test_coherence_min_is_none_until_baseline() -> None:
    # Remains None until a live baseline run sets it in S5.
    assert THRESHOLD_COHERENCE_MIN is None


def test_high_variance_max_pct_is_twenty_percent() -> None:
    assert THRESHOLD_HIGH_VARIANCE_MAX_PCT == 0.20
