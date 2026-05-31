"""Unit tests for scorer cost-display formatting."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "evals"))

from optimizer.scorer import (
    _cache_summary,
    _format_provider_spend,
    _provider_from_model,
    _judge_model_summary,
    check_cross_profile_judge_consistency,
)


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


# ── _provider_from_model ──────────────────────────────────────────────────────


def test_provider_qwen35_nim_routes_nvidia() -> None:
    assert _provider_from_model("qwen/qwen3.5-397b-a17b") == "nvidia"


def test_provider_qwen_future_nim_variant_routes_nvidia() -> None:
    # Forward-compat: any future NIM Qwen with a slash must not fall through to groq.
    assert _provider_from_model("qwen/qwen3.6-future-variant") == "nvidia"


def test_provider_deepseek_nim_routes_nvidia() -> None:
    assert _provider_from_model("deepseek-ai/deepseek-v4-flash") == "nvidia"


def test_provider_gpt_oss_groq_routes_groq() -> None:
    # Groq hosts OpenAI open-weight models under openai/ namespace — not NIM.
    assert _provider_from_model("openai/gpt-oss-120b") == "groq"


def test_provider_llama_groq_routes_groq() -> None:
    assert _provider_from_model("llama-3.3-70b-versatile") == "groq"


def test_provider_qwen_bare_groq_routes_groq() -> None:
    assert _provider_from_model("qwen3-32b") == "groq"


# ── _cache_summary ────────────────────────────────────────────────────────────


def test_cache_summary_all_zeros_when_fields_absent() -> None:
    """Records without cache fields sum to zero — backward-compatible with old JSONL."""
    records = [
        {"completed": True},
        {"completed": True, "archetypes": [{}]},
    ]
    result = _cache_summary(records)
    assert result["cache_read_tokens"] == 0
    assert result["cache_write_tokens"] == 0
    assert result["cache_hit_rate"] == 0.0


def test_cache_summary_with_cache_hits() -> None:
    """Records with cache hits produce correct hit rate."""
    # input_tokens_actual = 2000, cache_write = 500, cache_read = 1000
    # standard_input = 2000 - 500 - 1000 = 500
    # denom = cache_read + standard_input = 1000 + 500 = 1500
    # hit_rate = 1000 / 1500 = 0.667
    records = [
        {
            "completed": True,
            "cache_read_tokens": 1000,
            "cache_write_tokens": 500,
            "input_tokens_actual": 2000,
        }
    ]
    result = _cache_summary(records)
    assert result["cache_read_tokens"] == 1000
    assert result["cache_write_tokens"] == 500
    assert result["cache_hit_rate"] == pytest.approx(0.667, abs=0.001)


def test_cache_summary_skips_incomplete_records() -> None:
    """Incomplete records (completed=False or absent) are excluded from totals."""
    records = [
        {"completed": True, "cache_read_tokens": 100, "input_tokens_actual": 500},
        {"completed": False, "cache_read_tokens": 9999, "input_tokens_actual": 9999},
        {"cache_read_tokens": 8888, "input_tokens_actual": 8888},  # completed absent
    ]
    result = _cache_summary(records)
    assert result["cache_read_tokens"] == 100


def test_cache_summary_zero_hit_rate_when_only_writes() -> None:
    """If only cache writes (first call, no reads yet), hit rate is 0."""
    records = [
        {
            "completed": True,
            "cache_read_tokens": 0,
            "cache_write_tokens": 1451,
            "input_tokens_actual": 1451,
        }
    ]
    result = _cache_summary(records)
    assert result["cache_hit_rate"] == 0.0


# ── _judge_model_summary ──────────────────────────────────────────────────────


def test_judge_model_summary_empty_records() -> None:
    assert _judge_model_summary([]) == {"judge_models": [], "unknown_count": 0, "mixed": False}


def test_judge_model_summary_single_judge() -> None:
    scored = [
        {"judge_scores": [{"judge_model": "eval-judge-qwen3-32b"}]},
        {"judge_scores": [{"judge_model": "eval-judge-qwen3-32b"}]},
    ]
    result = _judge_model_summary(scored)
    assert result["judge_models"] == ["eval-judge-qwen3-32b"]
    assert result["unknown_count"] == 0
    assert result["mixed"] is False


def test_judge_model_summary_mixed_judges() -> None:
    scored = [
        {"judge_scores": [{"judge_model": "eval-judge-qwen3-32b"}]},
        {"judge_scores": [{"judge_model": "eval-judge-sonnet"}]},
    ]
    result = _judge_model_summary(scored)
    assert set(result["judge_models"]) == {"eval-judge-qwen3-32b", "eval-judge-sonnet"}
    assert result["mixed"] is True


def test_judge_model_summary_unknown_entries() -> None:
    """Legacy entries with judge_model="" count as unknown, not as a model name."""
    scored = [{"judge_scores": [{"judge_model": ""}, {"judge_model": "eval-judge-qwen3-32b"}]}]
    result = _judge_model_summary(scored)
    assert result["judge_models"] == ["eval-judge-qwen3-32b"]
    assert result["unknown_count"] == 1
    assert result["mixed"] is False  # only one known model, unknown doesn't count as a second


def test_judge_model_summary_all_unknown() -> None:
    scored = [{"judge_scores": [{"judge_model": ""}, {"judge_model": ""}]}]
    result = _judge_model_summary(scored)
    assert result["judge_models"] == []
    assert result["unknown_count"] == 2
    assert result["mixed"] is False


# ── check_cross_profile_judge_consistency ─────────────────────────────────────


def _make_summary(
    profile: str,
    coherence_avg: float | None = None,
    judge_models: list[str] | None = None,
    judge_unknown_count: int = 0,
    judge_mixed: bool = False,
) -> dict:
    return {
        "profile": profile,
        "coherence_avg": coherence_avg,
        "judge_models": judge_models or [],
        "judge_unknown_count": judge_unknown_count,
        "judge_mixed": judge_mixed,
    }


def test_cross_profile_gate_no_coherence_data() -> None:
    """If no summaries have coherence data, gate passes vacuously."""
    summaries = [_make_summary("demo-llama"), _make_summary("demo-gpt-oss-120b")]
    ok, msg = check_cross_profile_judge_consistency(summaries)
    assert ok is True
    assert msg == ""


def test_cross_profile_gate_same_judge_passes() -> None:
    """All profiles used the same known judge — comparison valid."""
    summaries = [
        _make_summary("demo-llama", coherence_avg=3.5, judge_models=["eval-judge-qwen3-32b"]),
        _make_summary("demo-gpt-oss-120b", coherence_avg=3.8, judge_models=["eval-judge-qwen3-32b"]),
    ]
    ok, msg = check_cross_profile_judge_consistency(summaries)
    assert ok is True
    assert msg == ""


def test_cross_profile_gate_different_judges_fails() -> None:
    """Profiles used different judges — gate refuses."""
    summaries = [
        _make_summary("demo-llama", coherence_avg=3.5, judge_models=["eval-judge-qwen3-32b"]),
        _make_summary("demo-gpt-oss-120b", coherence_avg=3.8, judge_models=["eval-judge-sonnet"]),
    ]
    ok, msg = check_cross_profile_judge_consistency(summaries)
    assert ok is False
    assert "mismatch" in msg.lower() or "different" in msg.lower()


def test_cross_profile_gate_all_unknown_fails() -> None:
    """All entries have unknown judge (legacy cache) — gate refuses."""
    summaries = [
        _make_summary("demo-llama", coherence_avg=3.5, judge_unknown_count=6),
        _make_summary("demo-gpt-oss-120b", coherence_avg=3.8, judge_unknown_count=6),
    ]
    ok, msg = check_cross_profile_judge_consistency(summaries)
    assert ok is False
    assert "unknown" in msg.lower() or "legacy" in msg.lower()


def test_cross_profile_gate_unknown_mixed_with_known_warns() -> None:
    """One known judge + some legacy unknowns → gate passes with a warning."""
    summaries = [
        _make_summary("demo-llama", coherence_avg=3.5,
                      judge_models=["eval-judge-qwen3-32b"], judge_unknown_count=2),
        _make_summary("demo-gpt-oss-120b", coherence_avg=3.8,
                      judge_models=["eval-judge-qwen3-32b"], judge_unknown_count=1),
    ]
    ok, msg = check_cross_profile_judge_consistency(summaries)
    assert ok is True
    assert "legacy" in msg.lower() or "unknown" in msg.lower()


def test_cross_profile_gate_within_run_mix_fails() -> None:
    """A single profile with mixed judges within its run → gate refuses."""
    summaries = [
        _make_summary("demo-llama", coherence_avg=3.5,
                      judge_models=["eval-judge-qwen3-32b", "eval-judge-sonnet"],
                      judge_mixed=True),
        _make_summary("demo-gpt-oss-120b", coherence_avg=3.8,
                      judge_models=["eval-judge-sonnet"]),
    ]
    ok, msg = check_cross_profile_judge_consistency(summaries)
    assert ok is False
    assert "mixed" in msg.lower()
