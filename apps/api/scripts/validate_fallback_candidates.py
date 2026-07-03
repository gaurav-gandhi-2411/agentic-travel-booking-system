"""Candidate validation for the LLM provider fallback chain (spec.md).

Runs OUR REAL planner (extract_travel_intent) and optimizer
(generate_archetype_explanation / generate_archetype_comparisons) tool
schemas against a shortlist of free OpenRouter models, via the real
OpenRouterAdapter (no mocking). Checks the exact failure modes that ruled
out GPT-OSS-120B:

1. Tool-call JSON parses cleanly (parse_openai_tool_calls does json.loads
   with no try/except upstream of it — a malformed-JSON tool call raises here).
2. No null returned for a schema field that is NOT typed nullable (GPT-OSS
   returned null for non-nullable numeric fields -> Groq 400).
3. No U+2011 (non-breaking hyphen) or other suspicious non-ASCII punctuation
   in generated string fields.
4. Field types/patterns match the schema (IATA pattern, enum membership).

This is a read-only validation run — it does not wire anything into the
routing config. Report is printed; nothing is cached or persisted.

Usage (from apps/api):
    python -m scripts.validate_fallback_candidates
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

# Windows console (cp1252) chokes on unicode punctuation that shows up in model
# output (en-dash, curly quotes, etc.) — that's literally part of what we're
# checking for, so the report itself must not crash on it.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from travel_agent.agents.tools import (  # noqa: E402
    EXTRACT_TRAVEL_INTENT,
    GENERATE_ARCHETYPE_COMPARISONS,
    GENERATE_ARCHETYPE_EXPLANATION,
)
from travel_agent.llm.base import Message  # noqa: E402
from travel_agent.llm.openrouter import OpenRouterAdapter  # noqa: E402

_REPO_ROOT_ENV = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(_REPO_ROOT_ENV)
load_dotenv(find_dotenv(usecwd=True))  # apps/api/.env is stale (Issue #49) — root .env loads first, wins

_PROMPTS = Path(__file__).parent.parent / "src" / "travel_agent" / "agents" / "prompts"
_PLANNER_SYSTEM = (_PROMPTS / "planner_system.txt").read_text().replace("{today}", "2026-07-01")
_OPTIMIZER_SYSTEM = (_PROMPTS / "optimizer_system.txt").read_text().replace("{today}", "2026-07-01")

# GPT-OSS-120B is RULED OUT — do not re-add without new evidence. See spec.md.
#
# Round 1 (4-way concurrent) already ruled OUT:
#   nvidia/nemotron-3-super-120b-a12b:free — CONFIRMED incompatible: leaks chain-of-thought
#     as plain content instead of calling the tool (0/2 optimizer calls produced a tool_call;
#     4/4 planner calls were clean). Same failure family as GPT-OSS — doesn't reliably emit
#     tool calls under tool_choice="auto". Ruled out on real evidence, not retested.
#   qwen/qwen3-next-80b-a3b-instruct:free — INCONCLUSIVE: 429/connection-error on every call
#     under 4-way concurrency, zero clean signal. Deprioritized; not worth a dedicated re-run
#     given llama + gemma already cover chain positions #2 and #3.
#
# Round 2: meta-llama/llama-3.3-70b-instruct:free re-tested alone (required at chain
#   position #2 by spec regardless of outcome) — still 0/16, all 429 (oversubscribed
#   upstream, not a schema issue) -> INCONCLUSIVE, effectively dead as a fallback today.
#   google/gemma-4-31b-it:free -> PASS, 10/10 clean (the baseline to beat).
#
# Round 3 (2026-07-04) — current live free roster confirmed via GET
# https://openrouter.ai/api/v1/models (supported_parameters includes "tools").
# NOTE: no DeepSeek model has a :free variant on OpenRouter right now (all
# deepseek/* ids are paid, albeit cheap) -- deepseek-v4-flash is NOT a free
# candidate today, so it is excluded here. Testing the current roster for
# BOTH schema compatibility and availability (success rate), not just schema:
#   - google/gemma-4-31b-it:free           -- re-run: confirm still clean + get a
#       real availability number (round 1's was small-N).
#   - meta-llama/llama-3.3-70b-instruct:free -- quick re-check; mandatory at chain
#       position #2 regardless of pass/fail (same model as Groq primary).
#   - qwen/qwen3-coder:free                -- new candidate, native tool-use model.
#   - google/gemma-4-26b-a4b-it:free        -- smaller Gemma sibling; same family as
#       the known-clean 31B, testing whether it's also more available.
#   - nvidia/nemotron-3-nano-30b-a3b:free   -- new NVIDIA nano; different family from
#       the ruled-out 120B nemotron, worth an independent schema check.
CANDIDATES = [
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-coder:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
]

PLANNER_CASES = [
    ("w2-p-001", "fly from Delhi to Dubai next month"),  # baseline: budget_inr null (nullable)
    ("w2-p-013", "Air India morning flights from Delhi to London, premium economy, 2 people, September 2026"),
    ("w2-p-014", "business class trip to Paris for 2 people, 3 lakhs per person, from Mumbai in October 2026"),
    ("w2-p-021", "economy round trip from Delhi to Amsterdam, no red-eye flights, in November 2026"),
]

_SUSPICIOUS_UNICODE = {
    "‑": "U+2011 NON-BREAKING HYPHEN",
    "–": "U+2013 EN DASH",
    "—": "U+2014 EM DASH",
    "‘": "U+2018 LEFT SINGLE QUOTE",
    "’": "U+2019 RIGHT SINGLE QUOTE",
    "“": "U+201C LEFT DOUBLE QUOTE",
    "”": "U+201D RIGHT DOUBLE QUOTE",
}

_OPTIMIZER_FLIGHT_A = (
    "Archetype: best_value\nRoute: BOM -> NRT\nAirline: QR  Flight: QR-147\n"
    "Price: INR 22,000\nDuration: 12h 0m  |  2 stop(s)\nRefundable: False\nCabin: economy"
)
_OPTIMIZER_FLIGHT_B = (
    "Archetype: best_experience\nRoute: BOM -> NRT\nAirline: SQ  Flight: SQ-401\n"
    "Price: INR 48,000\nDuration: 7h 0m  |  non-stop\nRefundable: False\nCabin: economy"
)


@dataclass
class CaseResult:
    case_id: str
    tool: str
    ok: bool
    detail: str = ""


@dataclass
class CandidateReport:
    model: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)


def _check_non_nullable_nulls(raw: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Flag any property returned as null whose schema type does NOT include 'null'."""
    violations = []
    props = schema["properties"]
    for key, val in raw.items():
        if key not in props or val is not None:
            continue
        prop_type = props[key].get("type")
        allows_null = isinstance(prop_type, list) and "null" in prop_type
        if not allows_null:
            violations.append(f"{key}=null but schema type is {prop_type!r} (non-nullable)")
    return violations


def _check_required_present(raw: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    missing = [k for k in schema.get("required", []) if k not in raw]
    return [f"missing required field {k!r}" for k in missing]


def _check_pattern(raw: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    violations = []
    props = schema["properties"]
    for key, val in raw.items():
        if val is None or key not in props:
            continue
        pattern = props[key].get("pattern")
        if pattern and isinstance(val, str) and not re.match(pattern, val):
            violations.append(f"{key}={val!r} fails pattern {pattern!r}")
        enum = props[key].get("enum")
        if enum and val not in enum:
            violations.append(f"{key}={val!r} not in enum {enum!r}")
    return violations


def _check_unicode(raw: dict[str, Any]) -> list[str]:
    violations = []
    for key, val in raw.items():
        if not isinstance(val, str):
            continue
        for ch, name in _SUSPICIOUS_UNICODE.items():
            if ch in val:
                violations.append(f"{key} contains {name} ({ch!r})")
    return violations


_RETRY_AFTER_RE = re.compile(r"retry_after_seconds['\"]?:\s*(\d+)")
_MAX_RETRIES = 3


async def _chat_with_retry(adapter: OpenRouterAdapter, *args: Any, **kwargs: Any) -> Any:
    """adapter.chat with 429 retry — free-tier OpenRouter models (Venice-hosted)
    allow roughly one request per ~30s per model; retry using the provider's own
    retry_after_seconds instead of guessing."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await adapter.chat(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - inspecting for 429 vs real failure
            last_exc = exc
            msg = str(exc)
            if "429" not in msg and "rate-limited" not in msg:
                raise
            match = _RETRY_AFTER_RE.search(msg)
            wait_s = int(match.group(1)) + 2 if match else 32
            print(f"    [429] retrying in {wait_s}s (attempt {attempt + 1}/{_MAX_RETRIES})")
            await asyncio.sleep(wait_s)
    raise last_exc  # type: ignore[misc]


async def _run_planner_case(
    adapter: OpenRouterAdapter, model: str, case_id: str, query: str
) -> CaseResult:
    try:
        response = await _chat_with_retry(
            adapter,
            [Message(role="user", content=query)],
            model=model,
            max_tokens=1024,
            temperature=0.0,
            system=_PLANNER_SYSTEM,
            tools=[EXTRACT_TRAVEL_INTENT],
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad: this IS the check
        return CaseResult(case_id, "planner", False, f"call/parse failed: {type(exc).__name__}: {exc}")

    if not response.tool_calls:
        return CaseResult(case_id, "planner", False, f"no tool call; content={response.content!r}")

    call = response.tool_calls[0]
    if call.name != EXTRACT_TRAVEL_INTENT.name:
        return CaseResult(case_id, "planner", False, f"wrong tool name: {call.name!r}")

    violations = (
        _check_required_present(call.input, EXTRACT_TRAVEL_INTENT.input_schema)
        + _check_non_nullable_nulls(call.input, EXTRACT_TRAVEL_INTENT.input_schema)
        + _check_pattern(call.input, EXTRACT_TRAVEL_INTENT.input_schema)
        + _check_unicode(call.input)
    )
    if violations:
        return CaseResult(case_id, "planner", False, "; ".join(violations))
    return CaseResult(case_id, "planner", True)


async def _run_optimizer_explain(adapter: OpenRouterAdapter, model: str) -> CaseResult:
    try:
        response = await _chat_with_retry(
            adapter,
            [Message(role="user", content=_OPTIMIZER_FLIGHT_A)],
            model=model,
            max_tokens=256,
            temperature=0.3,
            system=_OPTIMIZER_SYSTEM,
            tools=[GENERATE_ARCHETYPE_EXPLANATION],
        )
    except Exception as exc:  # noqa: BLE001
        return CaseResult("optimizer_explain", "optimizer", False, f"{type(exc).__name__}: {exc}")

    if not response.tool_calls:
        return CaseResult("optimizer_explain", "optimizer", False, f"no tool call; content={response.content!r}")
    call = response.tool_calls[0]
    schema = GENERATE_ARCHETYPE_EXPLANATION.input_schema
    violations = (
        _check_required_present(call.input, schema)
        + _check_non_nullable_nulls(call.input, schema)
        + _check_unicode(call.input)
    )
    text = str(call.input.get("explanation", ""))
    if not (schema["properties"]["explanation"]["minLength"] <= len(text) <= schema["properties"]["explanation"]["maxLength"]):
        violations.append(f"explanation length {len(text)} outside schema bounds")
    if violations:
        return CaseResult("optimizer_explain", "optimizer", False, "; ".join(violations))
    return CaseResult("optimizer_explain", "optimizer", True)


async def _run_optimizer_compare(adapter: OpenRouterAdapter, model: str) -> CaseResult:
    content = (
        "Generate comparisons for these two archetypes:\n\n"
        f"=== BEST VALUE ===\n{_OPTIMIZER_FLIGHT_A}\n\n"
        f"=== BEST EXPERIENCE ===\n{_OPTIMIZER_FLIGHT_B}"
    )
    try:
        response = await _chat_with_retry(
            adapter,
            [Message(role="user", content=content)],
            model=model,
            max_tokens=512,
            temperature=0.3,
            system=_OPTIMIZER_SYSTEM,
            tools=[GENERATE_ARCHETYPE_COMPARISONS],
        )
    except Exception as exc:  # noqa: BLE001
        return CaseResult("optimizer_compare", "optimizer", False, f"{type(exc).__name__}: {exc}")

    if not response.tool_calls:
        return CaseResult("optimizer_compare", "optimizer", False, f"no tool call; content={response.content!r}")
    call = response.tool_calls[0]
    schema = GENERATE_ARCHETYPE_COMPARISONS.input_schema
    violations = (
        _check_required_present(call.input, schema)
        + _check_non_nullable_nulls(call.input, schema)
        + _check_unicode(call.input)
    )
    if violations:
        return CaseResult("optimizer_compare", "optimizer", False, "; ".join(violations))
    return CaseResult("optimizer_compare", "optimizer", True)


async def validate_candidate(model: str) -> CandidateReport:
    adapter = OpenRouterAdapter()
    # A hung free-tier endpoint must not stall the whole validation run — cap each
    # HTTP call (openai SDK has no timeout set by default in OpenRouterAdapter).
    adapter._client = adapter._client.with_options(timeout=45.0)  # noqa: SLF001
    report = CandidateReport(model=model)
    for case_id, query in PLANNER_CASES:
        result = await _run_planner_case(adapter, model, case_id, query)
        report.results.append(result)
        await asyncio.sleep(5.0)  # free-tier (Venice) allows ~1 req/30s/model; retry handles the rest
    report.results.append(await _run_optimizer_explain(adapter, model))
    await asyncio.sleep(5.0)
    report.results.append(await _run_optimizer_compare(adapter, model))
    return report


def _print_report(reports: list[CandidateReport]) -> None:
    print("\n" + "=" * 78)
    print("FALLBACK CANDIDATE VALIDATION — planner + optimizer real tool schemas")
    print("=" * 78)
    for r in reports:
        status = "PASS — all cases clean" if r.passed else "FAIL"
        print(f"\n[{status}] {r.model}")
        for case in r.results:
            mark = "  ok " if case.ok else " FAIL"
            print(f"  {mark}  {case.tool:10s} {case.case_id:20s} {case.detail}")
    print("\n" + "=" * 78)
    passing = [r.model for r in reports if r.passed]
    failing = [r.model for r in reports if not r.passed]
    print(f"PASSING ({len(passing)}): {passing}")
    print(f"FAILING ({len(failing)}): {failing}")
    print("=" * 78)


async def _validate_and_announce(model: str) -> CandidateReport:
    print(f">>> validating {model} ...")
    report = await validate_candidate(model)
    print(f"<<< done {model}: {'PASS' if report.passed else 'FAIL'}")
    return report


async def main() -> int:
    # Each free-tier model is rate-limited independently (~1 req/30-45s per model,
    # confirmed empirically) — running candidates concurrently instead of sequentially
    # cuts wall-clock roughly N-fold instead of serializing all of them.
    reports = list(await asyncio.gather(*(_validate_and_announce(m) for m in CANDIDATES)))
    _print_report(reports)
    return 0 if any(r.passed for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
