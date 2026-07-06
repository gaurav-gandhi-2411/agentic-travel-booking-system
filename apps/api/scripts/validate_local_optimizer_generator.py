"""Candidate validation for the LOCAL Wave 2 optimizer-explanation generator.

Groq structurally cannot hold the Wave 2 optimizer step (93 calls, ~104k
tokens) in one 100k-token/day window -- this is true even on a fully fresh
day, not a quota-exhaustion problem. Path B moves optimizer EXPLANATION
generation to a local Ollama model to unblock the baseline; planner
extraction stays canonical on Groq (separate, much smaller budget).

The generator model MUST be a DIFFERENT family than the Tier-2 judge
(qwen3:8b, Alibaba) -- using the same model to both generate and judge
explanations is self-grading bias, the exact thing the judge's cross-family
choice was meant to avoid. qwen3:30b-a3b is excluded for the same reason
(same "qwen3" lineage, just a bigger MoE variant).

Runs our REAL generate_archetype_explanation / generate_archetype_comparisons
tool schemas against a shortlist of already-pulled, tool-capable, different-
family Ollama models -- mirrors scripts/validate_fallback_candidates.py's
checks (no null-for-non-nullable, no suspicious unicode, schema length/
pattern bounds) but via OllamaAdapter instead of OpenRouterAdapter.

Read-only: does not wire anything into llm_routing.yaml or generate any of
the 93 real Wave 2 calls. Report is printed; nothing is cached.

Usage (from apps/api, requires Ollama running):
    python -m scripts.validate_local_optimizer_generator
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from travel_agent.agents.tools import (  # noqa: E402
    GENERATE_ARCHETYPE_COMPARISONS,
    GENERATE_ARCHETYPE_EXPLANATION,
)
from travel_agent.llm.base import Message  # noqa: E402
from travel_agent.llm.ollama import OllamaAdapter  # noqa: E402

_PROMPTS = Path(__file__).parent.parent / "src" / "travel_agent" / "agents" / "prompts"
_OPTIMIZER_SYSTEM = (_PROMPTS / "optimizer_system.txt").read_text().replace("{today}", "2026-07-01")

# qwen3:8b is the Tier-2 JUDGE -- excluded here (self-grading bias).
# qwen3:30b-a3b excluded too: same "qwen3" family, just a bigger MoE variant.
# gemma2:9b excluded: Ollama lists no "tools" capability for it (no native
# tool-call chat template) -- would very likely never emit a tool_calls
# response at all, wasting the run rather than just scoring lower.
CANDIDATES = [
    "llama3.1:8b",  # different family (Meta Llama vs Alibaba Qwen3), tools-capable, already pulled
    "llama3.2:3b",  # same family, smaller -- backup only if 3.1:8b fails
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

# Real flight summaries -- the exact string shape OptimizerAgent._flight_summary
# builds. One default-pool pair (the case every one of the 38 golden cases
# without an "optimizer_pool" override exercises) plus one pair drawn from the
# w2-p-030 varied pool (a genuinely different, non-default shape) so the
# validation isn't just testing the one pool every other candidate script saw.
_DEFAULT_VALUE = (
    "Archetype: best-value\nRoute: BOM -> NRT\nAirline: QR  Flight: QR-147\n"
    "Price: INR 22,000\nDuration: 12h 0m  |  2 stop(s)\nRefundable: False\nCabin: economy"
)
_DEFAULT_EXPERIENCE = (
    "Archetype: best-experience\nRoute: BOM -> NRT\nAirline: AI  Flight: AI-301\n"
    "Price: INR 35,000\nDuration: 8h 0m  |  non-stop\nRefundable: False\nCabin: economy"
)
_VARIEDPOOL_VALUE = (
    "Archetype: best-value\nRoute: DEL -> LHR\nAirline: YY  Flight: YY-100\n"
    "Price: INR 15,000\nDuration: 12h 0m  |  2 stop(s)\nRefundable: False\nCabin: economy"
)
_VARIEDPOOL_EXPERIENCE = (
    "Archetype: best-experience\nRoute: DEL -> LHR\nAirline: YY  Flight: YY-400\n"
    "Price: INR 45,000\nDuration: 5h 0m  |  non-stop\nRefundable: False\nCabin: economy"
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


def _check_required_present(raw: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    missing = [k for k in schema.get("required", []) if k not in raw]
    return [f"missing required field {k!r}" for k in missing]


def _check_non_nullable_nulls(raw: dict[str, Any], schema: dict[str, Any]) -> list[str]:
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


def _check_unicode(raw: dict[str, Any]) -> list[str]:
    violations = []
    for key, val in raw.items():
        if not isinstance(val, str):
            continue
        for ch, name in _SUSPICIOUS_UNICODE.items():
            if ch in val:
                violations.append(f"{key} contains {name} ({ch!r})")
    return violations


def _check_length(raw: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    violations = []
    props = schema["properties"]
    for key, val in raw.items():
        if key not in props or not isinstance(val, str):
            continue
        lo, hi = props[key].get("minLength"), props[key].get("maxLength")
        if lo is not None and len(val) < lo:
            violations.append(f"{key} length {len(val)} < minLength {lo}")
        if hi is not None and len(val) > hi:
            violations.append(f"{key} length {len(val)} > maxLength {hi}")
    return violations


async def _run_explain(adapter: OllamaAdapter, model: str, case_id: str, summary: str) -> CaseResult:
    try:
        response = await adapter.chat(
            [Message(role="user", content=summary)],
            model=model,
            max_tokens=256,
            temperature=0.3,
            system=_OPTIMIZER_SYSTEM,
            tools=[GENERATE_ARCHETYPE_EXPLANATION],
        )
    except Exception as exc:  # noqa: BLE001 - this IS the check
        return CaseResult(case_id, "optimizer_explain", False, f"{type(exc).__name__}: {exc}")

    if not response.tool_calls:
        return CaseResult(
            case_id, "optimizer_explain", False, f"no tool call; content={response.content!r}"
        )
    call = response.tool_calls[0]
    if call.name != GENERATE_ARCHETYPE_EXPLANATION.name:
        return CaseResult(case_id, "optimizer_explain", False, f"wrong tool name: {call.name!r}")

    schema = GENERATE_ARCHETYPE_EXPLANATION.input_schema
    violations = (
        _check_required_present(call.input, schema)
        + _check_non_nullable_nulls(call.input, schema)
        + _check_unicode(call.input)
        + _check_length(call.input, schema)
    )
    detail = repr(call.input.get("explanation", ""))[:80]
    if violations:
        return CaseResult(case_id, "optimizer_explain", False, "; ".join(violations))
    return CaseResult(case_id, "optimizer_explain", True, detail)


async def _run_compare(
    adapter: OllamaAdapter, model: str, case_id: str, value_summary: str, exp_summary: str
) -> CaseResult:
    content = (
        "Generate comparisons for these two archetypes:\n\n"
        f"=== BEST VALUE ===\n{value_summary}\n\n"
        f"=== BEST EXPERIENCE ===\n{exp_summary}"
    )
    try:
        response = await adapter.chat(
            [Message(role="user", content=content)],
            model=model,
            max_tokens=512,
            temperature=0.3,
            system=_OPTIMIZER_SYSTEM,
            tools=[GENERATE_ARCHETYPE_COMPARISONS],
        )
    except Exception as exc:  # noqa: BLE001
        return CaseResult(case_id, "optimizer_compare", False, f"{type(exc).__name__}: {exc}")

    if not response.tool_calls:
        return CaseResult(
            case_id, "optimizer_compare", False, f"no tool call; content={response.content!r}"
        )
    call = response.tool_calls[0]
    if call.name != GENERATE_ARCHETYPE_COMPARISONS.name:
        return CaseResult(case_id, "optimizer_compare", False, f"wrong tool name: {call.name!r}")

    schema = GENERATE_ARCHETYPE_COMPARISONS.input_schema
    violations = (
        _check_required_present(call.input, schema)
        + _check_non_nullable_nulls(call.input, schema)
        + _check_unicode(call.input)
        + _check_length(call.input, schema)
    )
    if violations:
        return CaseResult(case_id, "optimizer_compare", False, "; ".join(violations))
    return CaseResult(case_id, "optimizer_compare", True)


async def validate_candidate(model: str) -> CandidateReport:
    adapter = OllamaAdapter()
    report = CandidateReport(model=model)
    report.results.append(await _run_explain(adapter, model, "default_value", _DEFAULT_VALUE))
    report.results.append(await _run_explain(adapter, model, "default_experience", _DEFAULT_EXPERIENCE))
    report.results.append(
        await _run_compare(adapter, model, "default_compare", _DEFAULT_VALUE, _DEFAULT_EXPERIENCE)
    )
    report.results.append(
        await _run_explain(adapter, model, "variedpool_value", _VARIEDPOOL_VALUE)
    )
    report.results.append(
        await _run_explain(adapter, model, "variedpool_experience", _VARIEDPOOL_EXPERIENCE)
    )
    report.results.append(
        await _run_compare(
            adapter, model, "variedpool_compare", _VARIEDPOOL_VALUE, _VARIEDPOOL_EXPERIENCE
        )
    )
    return report


def _print_report(reports: list[CandidateReport]) -> None:
    print("\n" + "=" * 78)
    print("LOCAL OPTIMIZER-GENERATOR VALIDATION -- real tool schemas, via Ollama")
    print("=" * 78)
    for r in reports:
        status = "PASS -- all cases clean" if r.passed else "FAIL"
        print(f"\n[{status}] {r.model}")
        for case in r.results:
            mark = "  ok " if case.ok else " FAIL"
            print(f"  {mark}  {case.tool:20s} {case.case_id:22s} {case.detail}")
    print("\n" + "=" * 78)
    passing = [r.model for r in reports if r.passed]
    failing = [r.model for r in reports if not r.passed]
    print(f"PASSING ({len(passing)}): {passing}")
    print(f"FAILING ({len(failing)}): {failing}")
    print("=" * 78)


async def main() -> int:
    reports = []
    for model in CANDIDATES:
        print(f">>> validating {model} ...")
        report = await validate_candidate(model)
        print(f"<<< done {model}: {'PASS' if report.passed else 'FAIL'}")
        reports.append(report)
    _print_report(reports)
    return 0 if any(r.passed for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
