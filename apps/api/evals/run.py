"""eval-quick runner for the PlannerAgent golden dataset.

Usage:
    python -m evals.run                          # VCR replay (CI default)
    python -m evals.run --live                   # real API calls (needs ANTHROPIC_API_KEY)
    python -m evals.run --dataset planner        # explicit dataset (default: planner)
    python -m evals.run --threshold 0.90         # override pass threshold (default 0.95)

Exit codes:
    0 — all checks pass (accuracy >= threshold)
    1 — accuracy below threshold
    2 — configuration / setup error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import vcr as vcrpy

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.state import RequestState
from travel_agent.llm.anthropic import AnthropicAdapter

_DATASETS_DIR = Path(__file__).parent / "datasets"
_CASSETTES_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "cassettes" / "eval"

_EVAL_DATE = date(2026, 5, 14)
_FLOAT_TOLERANCE = 0.01

_VCR = vcrpy.VCR(
    record_mode="none",
    filter_headers=[
        "authorization",
        "x-api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
        "anthropic-version",
        "anthropic-beta",
        "user-agent",
    ],
    decode_compressed_response=True,
    match_on=["method", "scheme", "host", "port", "path"],
)

# Fields scored in field-level accuracy (presence + value must match)
_SCORED_FIELDS: list[str] = [
    "origin_iata",
    "destination_iata",
    "cabin_class",
    "traveler_count",
    "trip_type",
]
# Optional fields — only scored when non-null in expected (presence penalty if missing)
_OPTIONAL_SCORED: list[str] = [
    "budget_inr",
    "hotel_min_stars",
    "airline_preference",
    "departure_time_constraint",
    "trip_duration_days",
]


def _load_golden(dataset: str) -> list[dict[str, Any]]:
    path = _DATASETS_DIR / dataset / "golden.jsonl"
    if not path.exists():
        print(f"ERROR: golden dataset not found at {path}", file=sys.stderr)
        sys.exit(2)
    examples = []
    with path.open() as f:
        for raw_line in f:
            stripped = raw_line.strip()
            if stripped:
                examples.append(json.loads(stripped))
    return examples


def _score_example(
    example_id: str,
    expected: dict[str, Any],
    intent: Any,
) -> dict[str, Any]:
    """Return per-field pass/fail for one example."""
    results: dict[str, Any] = {"id": example_id, "fields": {}}
    if intent is None:
        for f in _SCORED_FIELDS + _OPTIONAL_SCORED:
            if f in expected:
                results["fields"][f] = False
        return results

    intent_dict = intent.model_dump()

    for field in _SCORED_FIELDS:
        if field not in expected:
            continue
        got = intent_dict.get(field)
        want = expected[field]
        results["fields"][field] = got == want

    for field in _OPTIONAL_SCORED:
        if field not in expected or expected[field] is None:
            continue
        got = intent_dict.get(field)
        want = expected[field]
        if isinstance(want, float) and isinstance(got, (int, float)):
            results["fields"][field] = abs(float(got) - want) < _FLOAT_TOLERANCE
        else:
            results["fields"][field] = got == want

    return results


async def _run_example(
    example: dict[str, Any],
    agent: PlannerAgent,
    use_vcr: bool,
    dataset: str,
) -> dict[str, Any]:
    eid = example["id"]
    state = RequestState(raw_input=example["input"])

    if use_vcr:
        cassette = str(_CASSETTES_DIR / dataset / f"{eid}.yaml")
        with _VCR.use_cassette(cassette):
            result = await agent.run(state, today=_EVAL_DATE)
    else:
        result = await agent.run(state, today=_EVAL_DATE)

    if result.errors:
        print(f"  [{eid}] ERROR: {result.errors[0]}")

    return _score_example(eid, example["expected"], result.intent)


def _print_report(
    results: list[dict[str, Any]],
    threshold: float,
) -> tuple[float, bool]:
    field_passes: dict[str, list[bool]] = defaultdict(list)
    total_checks = 0
    total_pass = 0

    for r in results:
        for field, passed in r["fields"].items():
            field_passes[field].append(passed)
            total_checks += 1
            if passed:
                total_pass += 1

    overall = total_pass / total_checks if total_checks > 0 else 0.0

    print(f"\n{'-' * 50}")
    print(f"{'FIELD':<35} {'PASS':>6} {'TOTAL':>6} {'ACC':>7}")
    print(f"{'-' * 50}")
    for field in sorted(field_passes):
        passes = sum(field_passes[field])
        total = len(field_passes[field])
        acc = passes / total if total > 0 else 0.0
        marker = "" if acc >= threshold else " !"
        print(f"{field:<35} {passes:>6} {total:>6} {acc:>6.1%}{marker}")
    print(f"{'-' * 50}")
    print(f"{'OVERALL':<35} {total_pass:>6} {total_checks:>6} {overall:>6.1%}")
    print(f"{'-' * 50}")

    passed = overall >= threshold
    status = "PASS" if passed else "FAIL"
    print(f"\nResult: {status} (threshold={threshold:.0%}, accuracy={overall:.1%})")
    return overall, passed


async def _main(args: argparse.Namespace) -> int:
    examples = _load_golden(args.dataset)
    use_vcr = not args.live

    if use_vcr and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-vcr-replay-key"

    adapter = AnthropicAdapter()
    agent = PlannerAgent(adapter, "claude-haiku-4-5-20251001")

    print(
        f"eval-quick: {args.dataset} | {len(examples)} examples | "
        f"mode={'live' if args.live else 'vcr-replay'}"
    )

    results = []
    for ex in examples:
        scores = await _run_example(ex, agent, use_vcr, args.dataset)
        results.append(scores)

    _, passed = _print_report(results, args.threshold)
    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="eval-quick runner")
    parser.add_argument("--dataset", default="planner")
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--live", action="store_true", help="Use real API instead of VCR cassettes")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
