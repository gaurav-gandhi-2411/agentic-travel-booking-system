"""Unit tests for LLM cost telemetry pricing module."""

from __future__ import annotations

from travel_agent.observability.pricing import compute_cost


def test_haiku_cost() -> None:
    """Haiku cost: $0.80/Mtok input + $4.00/Mtok output."""
    cost = compute_cost("claude-haiku-4-5-20251001", 1000, 500)
    expected = 1000 * 0.80 / 1_000_000 + 500 * 4.00 / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_cache_read_cheaper_than_standard() -> None:
    """Cache-read tokens should cost less than standard input tokens."""
    cost_no_cache = compute_cost("claude-haiku-4-5-20251001", 1000, 0)
    cost_cached = compute_cost("claude-haiku-4-5-20251001", 0, 0, cache_read_tokens=1000)
    assert cost_cached < cost_no_cache


def test_unknown_model_returns_zero() -> None:
    """Unknown model names return 0.0 rather than raising."""
    assert compute_cost("unknown-model-xyz", 1000, 1000) == 0.0


def test_free_tier_model_returns_zero() -> None:
    """Free-tier models (Groq, OpenRouter) return 0.0 cost."""
    assert compute_cost("llama-3.3-70b-versatile", 5000, 1000) == 0.0


def test_sonnet_cost() -> None:
    """Sonnet-4-6 cost: $3.00/Mtok input + $15.00/Mtok output."""
    cost = compute_cost("claude-sonnet-4-6", 2000, 800)
    expected = 2000 * 3.00 / 1_000_000 + 800 * 15.00 / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_cache_write_cost() -> None:
    """Cache-write tokens for Haiku: 125% of input = $1.00/Mtok."""
    cost = compute_cost("claude-haiku-4-5", 0, 0, cache_read_tokens=0, cache_write_tokens=1000)
    expected = 1000 * 1.00 / 1_000_000
    assert abs(cost - expected) < 1e-9


def test_combined_tokens() -> None:
    """Mixed standard + cache tokens are computed correctly."""
    # 800 standard input, 200 cache-read, 100 cache-write, 400 output
    cost = compute_cost(
        "claude-haiku-4-5-20251001",
        input_tokens=1100,  # includes cache tokens in the raw total
        output_tokens=400,
        cache_read_tokens=200,
        cache_write_tokens=100,
    )
    # standard_input = max(0, 1100 - 200 - 100) = 800
    expected = (
        800 * 0.80 / 1_000_000
        + 400 * 4.00 / 1_000_000
        + 200 * 0.08 / 1_000_000
        + 100 * 1.00 / 1_000_000
    )
    assert abs(cost - expected) < 1e-9
