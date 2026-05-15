"""LLM pricing table for cost telemetry.

Rates sourced 2026-05-16. Refresh quarterly.
All rates are USD per 1,000,000 tokens ($/Mtok).
"""

from __future__ import annotations

from dataclasses import dataclass

PRICING_UPDATED = "2026-05-16"


@dataclass(frozen=True)
class TokenRates:
    input_per_mtok: float  # standard input tokens
    output_per_mtok: float
    cache_read_per_mtok: float = 0.0  # 10% of input for Anthropic
    cache_write_per_mtok: float = 0.0  # 125% of input for Anthropic


# Anthropic (https://www.anthropic.com/pricing, 2026-05-16)
# haiku-4-5: $0.80/Mtok input, $4.00/Mtok output
# cache read: 10% of input = $0.08/Mtok; cache write: 125% of input = $1.00/Mtok
# sonnet-4-6: $3.00/Mtok input, $15.00/Mtok output
_RATES: dict[str, TokenRates] = {
    "claude-haiku-4-5-20251001": TokenRates(0.80, 4.00, 0.08, 1.00),
    "claude-haiku-4-5": TokenRates(0.80, 4.00, 0.08, 1.00),  # alias
    "claude-sonnet-4-6": TokenRates(3.00, 15.00, 0.30, 3.75),
    # Groq (https://console.groq.com/docs/pricing, 2026-05-16)
    # llama-3.3-70b free tier — $0 for now; paid tier exists
    "llama-3.3-70b-versatile": TokenRates(0.0, 0.0),  # free tier
    # OpenRouter Qwen free (https://openrouter.ai/qwen/qwen-2.5-72b-instruct:free, 2026-05-16)
    "qwen/qwen-2.5-72b-instruct:free": TokenRates(0.0, 0.0),  # free tier
    "qwen-2.5-72b-instruct": TokenRates(0.0, 0.0),  # alias
}


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Return USD cost for a single LLM call. Returns 0.0 for unknown models."""
    rates = _RATES.get(model)
    if rates is None:
        # Try prefix match for versioned model names
        for key, r in _RATES.items():
            if model.startswith(key) or key.startswith(model):
                rates = r
                break
        if rates is None:
            return 0.0
    standard_input = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    cost = (
        standard_input * rates.input_per_mtok / 1_000_000
        + output_tokens * rates.output_per_mtok / 1_000_000
        + cache_read_tokens * rates.cache_read_per_mtok / 1_000_000
        + cache_write_tokens * rates.cache_write_per_mtok / 1_000_000
    )
    return round(cost, 8)
