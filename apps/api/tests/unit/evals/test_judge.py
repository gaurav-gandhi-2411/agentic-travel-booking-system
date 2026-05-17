"""Unit tests for evals/optimizer/judge.py (CoherenceJudge)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make both src and evals importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "evals"))

from optimizer.judge import (
    CoherenceJudge,
    JudgeScore,
    _cache_key,
    _extract_json,
    _strip_thinking,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

_SCENARIO = {
    "id": "opt-001",
    "route": "DEL-DXB",
    "window_start": "2026-06-01",
    "n_flights": 5,
    "flights": [],
}

_ARCHETYPE = {
    "label": "best-value",
    "flight": {
        "airline_code": "6E",
        "flight_number": "6E-1133",
        "origin_iata": "DEL",
        "destination_iata": "DXB",
        "price_inr": 18500,
        "outbound_duration_minutes": 480,
        "layover_count": 1,
        "is_refundable": False,
    },
    "explanation": (
        "This IndiGo flight offers the lowest price at ₹18,500 for budget-conscious "
        "travelers who can accept a 1-stop itinerary."
    ),
}

_GOOD_JUDGE_RESPONSE = json.dumps(
    {
        "score": 4,
        "reason": "Explanation mentions the specific price and stop count correctly.",
        "structural_valid": True,
    }
)


# ── _strip_thinking ──────────────────────────────────────────────────────────


def test_strip_thinking_removes_block() -> None:
    raw = "<think>internal reasoning here</think>Final answer"
    assert _strip_thinking(raw) == "Final answer"


def test_strip_thinking_no_think_tag_returns_raw() -> None:
    raw = "No thinking block here"
    assert _strip_thinking(raw) == raw


def test_strip_thinking_unclosed_tag_returns_empty() -> None:
    raw = "<think>unclosed block without closing tag"
    with patch("optimizer.judge._logger") as mock_logger:
        result = _strip_thinking(raw)
    assert result == ""
    mock_logger.warning.assert_called_once()


def test_strip_thinking_preserves_content_after_close() -> None:
    raw = "<think>x</think>  leading whitespace stripped  "
    assert _strip_thinking(raw) == "leading whitespace stripped"


# ── _extract_json ─────────────────────────────────────────────────────────────


def test_extract_json_strips_markdown_fences() -> None:
    raw = '```json\n{"score": 4}\n```'
    assert _extract_json(raw) == '{"score": 4}'


def test_extract_json_plain_json_unchanged() -> None:
    raw = '{"score": 3, "reason": "ok", "structural_valid": true}'
    assert _extract_json(raw) == raw


# ── _cache_key ────────────────────────────────────────────────────────────────


def test_cache_key_same_inputs_produce_same_key() -> None:
    k1 = _cache_key("opt-001", "best-value", "explanation text")
    k2 = _cache_key("opt-001", "best-value", "explanation text")
    assert k1 == k2


def test_cache_key_different_explanation_produces_different_key() -> None:
    k1 = _cache_key("opt-001", "best-value", "explanation A")
    k2 = _cache_key("opt-001", "best-value", "explanation B")
    assert k1 != k2


# ── CoherenceJudge mock tests ─────────────────────────────────────────────────


def _make_judge_with_mock_client(responses: list[str]) -> CoherenceJudge:
    """Build a CoherenceJudge whose _call_once returns the given responses in order."""
    mock_client = MagicMock()
    call_iter = iter(responses)

    async def mock_call_once(prompt: str) -> str:
        return next(call_iter)

    judge = object.__new__(CoherenceJudge)
    judge._model = "mock-model"
    judge._max_tokens = 512
    judge._client = mock_client
    judge._call_once = mock_call_once  # type: ignore[method-assign]
    return judge


@pytest.mark.asyncio
async def test_score_returns_expected_judge_score(tmp_path: Path) -> None:
    """Mock LLM returns a fixed score; verify JudgeScore fields."""
    responses = [_GOOD_JUDGE_RESPONSE] * 3
    judge = _make_judge_with_mock_client(responses)

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert isinstance(result, JudgeScore)
    assert result.coherence_score == 4
    assert result.structural_valid is True
    assert result.high_variance is False
    assert len(result.all_scores) == 3


@pytest.mark.asyncio
async def test_score_cache_hit_skips_llm(tmp_path: Path) -> None:
    """Second call with identical inputs hits cache, no LLM invocation."""
    responses = [_GOOD_JUDGE_RESPONSE] * 3
    judge = _make_judge_with_mock_client(responses)

    call_count = 0
    original = judge._call_once

    async def counting_call(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return await original(prompt)

    judge._call_once = counting_call  # type: ignore[method-assign]

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        await judge.score(_SCENARIO, _ARCHETYPE)
        await judge.score(_SCENARIO, _ARCHETYPE)  # should hit cache

    assert call_count == 3  # only the first call's 3 samples; second is cache


@pytest.mark.asyncio
async def test_score_median_of_three(tmp_path: Path) -> None:
    """Mock returns [3, 4, 4] → median == 4."""
    responses = [
        json.dumps({"score": 3, "reason": "ok", "structural_valid": True}),
        json.dumps({"score": 4, "reason": "ok", "structural_valid": True}),
        json.dumps({"score": 4, "reason": "ok", "structural_valid": True}),
    ]
    judge = _make_judge_with_mock_client(responses)

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert result.coherence_score == 4
    assert sorted(result.all_scores) == [3, 4, 4]
    assert result.high_variance is False


@pytest.mark.asyncio
async def test_score_high_variance_flag(tmp_path: Path) -> None:
    """Mock returns [2, 4, 5] → high_variance == True (range = 3 > 2)."""
    responses = [
        json.dumps({"score": 2, "reason": "bad", "structural_valid": False}),
        json.dumps({"score": 4, "reason": "ok", "structural_valid": True}),
        json.dumps({"score": 5, "reason": "great", "structural_valid": True}),
    ]
    judge = _make_judge_with_mock_client(responses)

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert result.high_variance is True
    assert sorted(result.all_scores) == [2, 4, 5]


@pytest.mark.asyncio
async def test_score_not_high_variance_when_range_two(tmp_path: Path) -> None:
    """Range == 2 is NOT flagged (threshold is > 2)."""
    responses = [
        json.dumps({"score": 3, "reason": "ok", "structural_valid": True}),
        json.dumps({"score": 5, "reason": "great", "structural_valid": True}),
        json.dumps({"score": 4, "reason": "ok", "structural_valid": True}),
    ]
    judge = _make_judge_with_mock_client(responses)

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert result.high_variance is False
