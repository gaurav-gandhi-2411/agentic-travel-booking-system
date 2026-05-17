"""LLM-as-judge for coherence scoring of OptimizerAgent archetype explanations.

Runs DeepSeek R1 Distill 70B (via Groq) with median-of-3 sampling.
Results are cached by hash of (scenario_id, archetype_label, explanation_text)
so re-runs are free.

Design: ADR-0016.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

# Make travel_agent importable from the eval context
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

_HIGH_VARIANCE_THRESHOLD = 2  # flag when max(scores) - min(scores) > this

_CACHE_DIR = Path(__file__).parent / "cache"
_CACHE_FILE = _CACHE_DIR / "judge_cache.json"
_logger = structlog.get_logger(__name__)

_JUDGE_SYSTEM_PROMPT = """\
You are an evaluation judge scoring travel-search archetype explanations on coherence.

Score 1-5 on this rubric:
5 = Explanation is logically coherent, references the specific flight's actual \
price/duration/stops, and the "ideal traveler" framing makes sense for that flight's tradeoffs.
4 = Coherent and references the flight's specifics, with minor framing weakness.
3 = Generally coherent but generic — could apply to many flights, not specifically this one.
2 = Has a logical break or factual mismatch with the flight's actual attributes.
1 = Incoherent, contradictory, or describes a different flight than the one shown.

Return JSON only — no markdown fences, no extra text:
{"score": int, "reason": str, "structural_valid": bool}

structural_valid is true when the explanation references the actual flight's \
attributes (price/duration/stops in the same ballpark). False if the explanation \
mentions details that clearly don't match the flight.\
"""


class JudgeScore(BaseModel):
    """Result of a single coherence judge call (median of 3 samples)."""

    coherence_score: int  # 1-5, median of 3 samples
    coherence_reason: str  # 1-2 sentence justification from best-median sample
    structural_valid: bool  # explanation references actual flight attributes
    raw_judge_output: str  # post-strip output of first call, for debugging
    high_variance: bool = False  # True when max(scores) - min(scores) > 2
    all_scores: list[int] = Field(default_factory=list)  # all 3 sample scores


# ── helpers ───────────────────────────────────────────────────────────────────


def _strip_thinking(raw: str) -> str:
    """Strip <think>...</think> blocks from reasoning model output (DeepSeek R1, Qwen3, etc.)."""
    match = re.search(r"</think>", raw, re.DOTALL)
    if match:
        return raw[match.end() :].strip()
    if "<think>" in raw:
        _logger.warning("judge_unclosed_think_tag: returning empty string")
        return ""
    return raw


def _extract_json(text: str) -> str:
    """Remove markdown code fences if the model wrapped its JSON."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return text.strip()


def _cache_key(scenario_id: str, archetype_label: str, explanation: str) -> str:
    payload = json.dumps([scenario_id, archetype_label, explanation], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _load_cache() -> dict[str, Any]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _build_user_prompt(scenario: dict, archetype: dict) -> str:
    flight = archetype.get("flight", {})
    price = flight.get("price_inr", "?")
    price_str = f"₹{price:,}" if isinstance(price, int) else str(price)
    return (
        f"Flight: {flight.get('airline_code', '?')} {flight.get('flight_number', '?')}\n"
        f"Route: {flight.get('origin_iata', '?')} → {flight.get('destination_iata', '?')}\n"
        f"Price: {price_str}\n"
        f"Duration: {flight.get('outbound_duration_minutes', '?')} min\n"
        f"Stops: {flight.get('layover_count', '?')}\n"
        f"Refundable: {flight.get('is_refundable', '?')}\n"
        f"Archetype label: {archetype.get('label', '?')}\n\n"
        f"Explanation to score:\n{archetype.get('explanation', '')}"
    )


# ── judge class ───────────────────────────────────────────────────────────────


class CoherenceJudge:
    """Scores archetype explanation quality via an LLM judge (median-of-3)."""

    def __init__(self, judge_profile: str = "eval-judge-qwen3-32b") -> None:
        from travel_agent.llm.routing import load_routing_config  # noqa: PLC0415

        profiles = load_routing_config()
        if judge_profile not in profiles:
            msg = (
                f"Unknown judge profile {judge_profile!r}. "
                f"Valid profiles: {sorted(profiles.keys())}"
            )
            raise ValueError(msg)

        profile = profiles[judge_profile]
        self._model: str = profile["model"]
        self._max_tokens: int = int(profile.get("max_tokens", 1024))

        provider = profile["provider"]
        if provider == "groq":
            from travel_agent.llm.groq import GroqAdapter  # noqa: PLC0415

            self._client = GroqAdapter()
        elif provider == "anthropic":
            from travel_agent.llm.anthropic import AnthropicAdapter  # noqa: PLC0415

            self._client = AnthropicAdapter()
        else:
            msg = f"Unsupported judge provider: {provider!r}"
            raise ValueError(msg)

    async def _call_once(self, user_prompt: str) -> str:
        """Single LLM call; returns stripped, fence-stripped text."""
        from travel_agent.llm.base import Message  # noqa: PLC0415

        messages: list[Message] = [Message(role="user", content=user_prompt)]
        resp = await self._client.chat(
            messages,
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=0.0,
            system=_JUDGE_SYSTEM_PROMPT,
        )
        return _extract_json(_strip_thinking(resp.content))

    async def score(self, scenario: dict, archetype: dict) -> JudgeScore:
        """Score one archetype explanation; uses cache then median-of-3."""
        scenario_id = scenario.get("id", "unknown")
        label = archetype.get("label", "")
        explanation = archetype.get("explanation", "")

        cache_key = _cache_key(scenario_id, label, explanation)
        cache = _load_cache()
        if cache_key in cache:
            return JudgeScore.model_validate(cache[cache_key])

        user_prompt = _build_user_prompt(scenario, archetype)

        raw_outputs: list[str] = []
        parsed_samples: list[dict] = []
        for _ in range(3):
            try:
                raw = await self._call_once(user_prompt)
                raw_outputs.append(raw)
                parsed_samples.append(json.loads(raw))
            except (json.JSONDecodeError, Exception) as exc:
                _logger.warning("judge_call_failed", error=str(exc))

        if not parsed_samples:
            result = JudgeScore(
                coherence_score=1,
                coherence_reason="Judge output could not be parsed after 3 attempts",
                structural_valid=False,
                raw_judge_output=raw_outputs[0] if raw_outputs else "",
                high_variance=False,
                all_scores=[],
            )
            cache[cache_key] = result.model_dump()
            _save_cache(cache)
            return result

        scores = [int(p.get("score", 1)) for p in parsed_samples]
        median_score = int(statistics.median(scores))
        score_range = max(scores) - min(scores)

        best = min(parsed_samples, key=lambda p: abs(int(p.get("score", 1)) - median_score))
        result = JudgeScore(
            coherence_score=median_score,
            coherence_reason=best.get("reason", ""),
            structural_valid=bool(best.get("structural_valid", True)),
            raw_judge_output=raw_outputs[0] if raw_outputs else "",
            high_variance=score_range > _HIGH_VARIANCE_THRESHOLD,
            all_scores=scores,
        )
        cache[cache_key] = result.model_dump()
        _save_cache(cache)
        return result


async def score_all_archetypes(
    records: list[dict],
    judge: CoherenceJudge,
) -> list[dict]:
    """Score all archetype explanations in a list of run records.

    Returns the same records with a 'judge_scores' key added (list of
    JudgeScore dicts, one per archetype).
    """
    out = []
    for rec in records:
        if "error" in rec or not rec.get("archetypes"):
            out.append({**rec, "judge_scores": []})
            continue
        scored_archetypes = []
        for arch in rec["archetypes"]:
            js = await judge.score(rec, arch)
            scored_archetypes.append(js.model_dump())
        out.append({**rec, "judge_scores": scored_archetypes})
    return out
