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
from optimizer.purge_poisoned_cache import purge_poisoned_entries

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
    judge._judge_profile = "mock-profile"
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


# ── judge_model and parse_failed fields ──────────────────────────────────────


@pytest.mark.asyncio
async def test_score_records_judge_model(tmp_path: Path) -> None:
    """judge_model is recorded in JudgeScore from the judge's profile name."""
    responses = [_GOOD_JUDGE_RESPONSE] * 3
    judge = _make_judge_with_mock_client(responses)

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert result.judge_model == "mock-profile"
    assert result.parse_failed is False


@pytest.mark.asyncio
async def test_score_parse_failure_sets_parse_failed(tmp_path: Path) -> None:
    """When all 3 judge calls fail to parse, parse_failed=True and all_scores=[]."""
    judge = _make_judge_with_mock_client(["not-json", "not-json", "not-json"])

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert result.parse_failed is True
    assert result.all_scores == []
    assert result.coherence_score == 1
    assert result.judge_model == "mock-profile"


@pytest.mark.asyncio
async def test_genuine_low_score_not_treated_as_poisoned(tmp_path: Path) -> None:
    """A real judge score of 1 has all_scores=[1,1,1] and parse_failed=False — not a cache miss."""
    responses = [
        json.dumps({"score": 1, "reason": "incoherent", "structural_valid": False}),
        json.dumps({"score": 1, "reason": "incoherent", "structural_valid": False}),
        json.dumps({"score": 1, "reason": "incoherent", "structural_valid": False}),
    ]
    judge = _make_judge_with_mock_client(responses)

    with (
        patch("optimizer.judge._CACHE_FILE", tmp_path / "cache.json"),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert result.coherence_score == 1
    assert result.all_scores == [1, 1, 1]
    assert result.parse_failed is False


@pytest.mark.asyncio
async def test_cache_rejects_poisoned_entry_reruns_judge(tmp_path: Path) -> None:
    """A poisoned cache entry (parse_failed=True) is bypassed; judge re-runs and returns fresh score."""
    cache_path = tmp_path / "cache.json"
    cache_dir = tmp_path

    # Pre-seed cache with a poisoned entry (parse_failed=True, all_scores=[])
    key = _cache_key(_SCENARIO["id"], _ARCHETYPE["label"], _ARCHETYPE["explanation"])
    poisoned_entry = {
        "coherence_score": 1,
        "coherence_reason": "Judge output could not be parsed after 3 attempts",
        "structural_valid": False,
        "raw_judge_output": "",
        "high_variance": False,
        "all_scores": [],
        "parse_failed": True,
        "judge_model": "mock-profile",
    }
    import json as _json
    cache_path.write_text(_json.dumps({key: poisoned_entry}), encoding="utf-8")

    # Set up judge with a real response
    call_count = 0
    responses = [_GOOD_JUDGE_RESPONSE] * 3
    judge = _make_judge_with_mock_client(responses)
    original_call = judge._call_once

    async def counting_call(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return await original_call(prompt)

    judge._call_once = counting_call  # type: ignore[method-assign]

    with (
        patch("optimizer.judge._CACHE_FILE", cache_path),
        patch("optimizer.judge._CACHE_DIR", cache_dir),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    # Judge was re-called (3 samples), not served from poisoned cache
    assert call_count == 3
    assert result.coherence_score == 4
    assert result.parse_failed is False


@pytest.mark.asyncio
async def test_cache_rejects_legacy_poisoned_entry(tmp_path: Path) -> None:
    """Legacy poisoned entry (all_scores=[], no parse_failed field) is also treated as cache miss."""
    cache_path = tmp_path / "cache.json"

    key = _cache_key(_SCENARIO["id"], _ARCHETYPE["label"], _ARCHETYPE["explanation"])
    # Simulate old-style poisoned entry: all_scores=[] but no parse_failed field
    legacy_poisoned = {
        "coherence_score": 1,
        "coherence_reason": "Judge output could not be parsed after 3 attempts",
        "structural_valid": False,
        "raw_judge_output": "",
        "high_variance": False,
        "all_scores": [],
        # no parse_failed field — simulates pre-fix cache entries
    }
    import json as _json
    cache_path.write_text(_json.dumps({key: legacy_poisoned}), encoding="utf-8")

    call_count = 0
    responses = [_GOOD_JUDGE_RESPONSE] * 3
    judge = _make_judge_with_mock_client(responses)
    original_call = judge._call_once

    async def counting_call(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return await original_call(prompt)

    judge._call_once = counting_call  # type: ignore[method-assign]

    with (
        patch("optimizer.judge._CACHE_FILE", cache_path),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    assert call_count == 3
    assert result.parse_failed is False


@pytest.mark.asyncio
async def test_cache_preserves_genuine_low_score(tmp_path: Path) -> None:
    """A genuine score of 1 (all_scores=[1,1,1], parse_failed=False) IS served from cache — no re-run."""
    cache_path = tmp_path / "cache.json"

    key = _cache_key(_SCENARIO["id"], _ARCHETYPE["label"], _ARCHETYPE["explanation"])
    genuine_low = {
        "coherence_score": 1,
        "coherence_reason": "Explanation is incoherent.",
        "structural_valid": False,
        "raw_judge_output": '{"score": 1, "reason": "incoherent", "structural_valid": false}',
        "high_variance": False,
        "all_scores": [1, 1, 1],
        "parse_failed": False,
        "judge_model": "eval-judge-qwen3-32b",
    }
    import json as _json
    cache_path.write_text(_json.dumps({key: genuine_low}), encoding="utf-8")

    call_count = 0
    judge = _make_judge_with_mock_client([_GOOD_JUDGE_RESPONSE] * 3)
    original_call = judge._call_once

    async def counting_call(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return await original_call(prompt)

    judge._call_once = counting_call  # type: ignore[method-assign]

    with (
        patch("optimizer.judge._CACHE_FILE", cache_path),
        patch("optimizer.judge._CACHE_DIR", tmp_path),
    ):
        result = await judge.score(_SCENARIO, _ARCHETYPE)

    # Served from cache — no LLM calls
    assert call_count == 0
    assert result.coherence_score == 1
    assert result.all_scores == [1, 1, 1]
    assert result.parse_failed is False


# ── purge_poisoned_entries ────────────────────────────────────────────────────


def test_purge_removes_poisoned_entries(tmp_path: Path) -> None:
    """Cleanup utility removes parse_failed=True entries."""
    import json as _json
    cache_path = tmp_path / "judge_cache.json"
    cache = {
        "aaa": {"coherence_score": 1, "all_scores": [], "parse_failed": True, "structural_valid": False, "coherence_reason": "failed", "raw_judge_output": "", "high_variance": False},
        "bbb": {"coherence_score": 4, "all_scores": [4, 4, 3], "parse_failed": False, "structural_valid": True, "coherence_reason": "good", "raw_judge_output": "", "high_variance": False},
        "ccc": {"coherence_score": 1, "all_scores": [], "parse_failed": True, "structural_valid": False, "coherence_reason": "failed", "raw_judge_output": "", "high_variance": False},
    }
    cache_path.write_text(_json.dumps(cache), encoding="utf-8")

    result = purge_poisoned_entries(cache_path)

    assert result["scanned"] == 3
    assert result["purged"] == 2
    assert result["retained"] == 1

    remaining = _json.loads(cache_path.read_text())
    assert "bbb" in remaining
    assert "aaa" not in remaining
    assert "ccc" not in remaining


def test_purge_removes_legacy_all_scores_empty(tmp_path: Path) -> None:
    """Cleanup also removes legacy entries with all_scores=[] even without parse_failed field."""
    import json as _json
    cache_path = tmp_path / "judge_cache.json"
    cache = {
        "legacy": {"coherence_score": 1, "all_scores": [], "structural_valid": False, "coherence_reason": "failed", "raw_judge_output": "", "high_variance": False},
        "good": {"coherence_score": 3, "all_scores": [3, 3, 4], "parse_failed": False, "structural_valid": True, "coherence_reason": "ok", "raw_judge_output": "", "high_variance": False},
    }
    cache_path.write_text(_json.dumps(cache), encoding="utf-8")

    result = purge_poisoned_entries(cache_path)
    assert result["purged"] == 1
    assert result["retained"] == 1


def test_purge_idempotent(tmp_path: Path) -> None:
    """Running purge twice yields 0 purged on second run."""
    import json as _json
    cache_path = tmp_path / "judge_cache.json"
    cache = {
        "p": {"coherence_score": 1, "all_scores": [], "parse_failed": True, "structural_valid": False, "coherence_reason": "x", "raw_judge_output": "", "high_variance": False},
    }
    cache_path.write_text(_json.dumps(cache), encoding="utf-8")

    first = purge_poisoned_entries(cache_path)
    second = purge_poisoned_entries(cache_path)

    assert first["purged"] == 1
    assert second["purged"] == 0
    assert second["retained"] == 0


def test_purge_genuine_low_score_not_purged(tmp_path: Path) -> None:
    """Genuine score=1 entry (all_scores=[1,1,1]) must NOT be purged."""
    import json as _json
    cache_path = tmp_path / "judge_cache.json"
    genuine_low = {
        "coherence_score": 1,
        "all_scores": [1, 1, 1],
        "parse_failed": False,
        "structural_valid": False,
        "coherence_reason": "incoherent explanation",
        "raw_judge_output": '{"score": 1}',
        "high_variance": False,
    }
    cache_path.write_text(_json.dumps({"x": genuine_low}), encoding="utf-8")

    result = purge_poisoned_entries(cache_path)
    assert result["purged"] == 0
    assert result["retained"] == 1
