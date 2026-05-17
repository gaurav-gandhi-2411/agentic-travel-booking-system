"""Unit tests for optimizer eval gate thresholds."""

from __future__ import annotations

from evals.optimizer.thresholds import (
    THRESHOLD_COHERENCE_MIN,
    THRESHOLD_COMPLETION_MIN,
    THRESHOLD_HIGH_VARIANCE_MAX_PCT,
    THRESHOLD_LABEL_CORRECT_COMPLETED,
)


def test_label_correct_completed_threshold_is_one() -> None:
    assert THRESHOLD_LABEL_CORRECT_COMPLETED == 1.0


def test_completion_min_threshold() -> None:
    assert THRESHOLD_COMPLETION_MIN == 0.83


def test_coherence_min_is_set_from_baseline() -> None:
    assert THRESHOLD_COHERENCE_MIN == 4.0


def test_high_variance_max_pct_is_twenty_percent() -> None:
    assert THRESHOLD_HIGH_VARIANCE_MAX_PCT == 0.20


def test_baseline_haiku_clears_all_thresholds() -> None:
    """Baseline numbers from 2026-05-17 must clear every gate."""
    haiku_completion = 24 / 24
    haiku_label_correct_on_completed = 24 / 24
    haiku_coherence_avg = 4.625
    haiku_hv_pct = 2 / 24

    assert haiku_completion >= THRESHOLD_COMPLETION_MIN
    assert haiku_label_correct_on_completed >= THRESHOLD_LABEL_CORRECT_COMPLETED
    assert haiku_coherence_avg >= THRESHOLD_COHERENCE_MIN
    assert haiku_hv_pct <= THRESHOLD_HIGH_VARIANCE_MAX_PCT


def test_baseline_llama_clears_all_thresholds() -> None:
    """Baseline numbers from 2026-05-17 must clear every gate."""
    llama_completion = 21 / 24
    llama_label_correct_on_completed = 21 / 21
    llama_coherence_avg = 4.571
    llama_hv_pct = 0 / 24

    assert llama_completion >= THRESHOLD_COMPLETION_MIN
    assert llama_label_correct_on_completed >= THRESHOLD_LABEL_CORRECT_COMPLETED
    assert llama_coherence_avg >= THRESHOLD_COHERENCE_MIN
    assert llama_hv_pct <= THRESHOLD_HIGH_VARIANCE_MAX_PCT
