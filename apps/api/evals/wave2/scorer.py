"""Wave 2 Tier-1 eval scorer — deterministic, zero-LLM, CI-runnable.

Reads a JSONL run file produced by evals.wave2.runner, scores all cases
deterministically, prints a report, and exits non-zero if required-field
accuracy falls below the threshold.

Usage:
    # Score latest run (CI default)
    python -m evals.wave2.scorer

    # Score specific run file
    python -m evals.wave2.scorer --run runs/20260701T120000_demo-llama.jsonl

    # Custom threshold
    python -m evals.wave2.scorer --threshold 0.90

    # Report-only (always exit 0)
    python -m evals.wave2.scorer --no-fail

Exit codes:
    0 — required-field accuracy >= threshold (or no baseline exists yet)
    1 — required-field accuracy < threshold
    2 — run file not found or not readable
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from travel_agent.agents.conversation_manager_types import ConversationAction, RefineArgs
from travel_agent.api.routes.refine import _apply_refine_filters
from travel_agent.coordinator.state import (
    CabinClass,
    FlightOption,
    TravelIntent,
    Window,
)

_GOLDEN_FILE = Path(__file__).parent / "golden.json"
_RUNS_DIR = Path(__file__).parent / "runs"
_REPORTS_DIR = Path(__file__).parent / "reports"

_REQUIRED_FIELDS: tuple[str, ...] = ("origin_iata", "destination_iata", "trip_type", "cabin_class")
_OPTIONAL_FIELDS: tuple[str, ...] = (
    "traveler_count",
    "budget_inr",
    "airline_preference",
    "departure_time_constraint",
    "trip_duration_days",
    "hotel_min_stars",
)
_FLOAT_TOL = 0.01
_DEFAULT_THRESHOLD = 0.80
_MORNING_START_HOUR = 6
_MORNING_END_HOUR = 12
_LOW_ACC_WARN = 0.80

# Synthetic flight pool for refine constraint checking.
# Must stay in sync with runner.SYNTHETIC_REFINE_POOL.
# Pool properties: [0] 07:30 0-stop 35k, [1] 14:00 1-stop 28k, [2] 22:00 2-stop 22k,
#                  [3] 18:00 0-stop 48k, [4] 09:00 1-stop 31k, [5] 23:30 0-stop 52k
_POOL_WINDOW = Window(start_date=date(2026, 9, 15), end_date=date(2026, 9, 21))
_SYNTHETIC_REFINE_POOL: list[FlightOption] = [
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T07:30:00",
        outbound_arrival_at="2026-09-16T03:30:00",
        airline_code="AI", flight_number="AI-301",
        cabin_class=CabinClass.ECONOMY, price_inr=35000,
        outbound_duration_minutes=480, layover_count=0,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T14:00:00",
        outbound_arrival_at="2026-09-16T12:00:00",
        airline_code="EK", flight_number="EK-502",
        cabin_class=CabinClass.ECONOMY, price_inr=28000,
        outbound_duration_minutes=540, layover_count=1,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T22:00:00",
        outbound_arrival_at="2026-09-16T22:00:00",
        airline_code="QR", flight_number="QR-147",
        cabin_class=CabinClass.ECONOMY, price_inr=22000,
        outbound_duration_minutes=720, layover_count=2,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T18:00:00",
        outbound_arrival_at="2026-09-16T14:00:00",
        airline_code="SQ", flight_number="SQ-401",
        cabin_class=CabinClass.ECONOMY, price_inr=48000,
        outbound_duration_minutes=420, layover_count=0,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T09:00:00",
        outbound_arrival_at="2026-09-16T07:00:00",
        airline_code="9W", flight_number="9W-802",
        cabin_class=CabinClass.ECONOMY, price_inr=31000,
        outbound_duration_minutes=600, layover_count=1,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T23:30:00",
        outbound_arrival_at="2026-09-16T23:00:00",
        airline_code="TK", flight_number="TK-221",
        cabin_class=CabinClass.ECONOMY, price_inr=52000,
        outbound_duration_minutes=390, layover_count=0,
    ),
]


def _load_golden() -> dict[str, dict[str, Any]]:
    cases = json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))
    return {c["id"]: c for c in cases}


def _score_field(field: str, expected: Any, got: Any) -> bool:
    if expected is None:
        return got is None
    if got is None:
        return False
    if field == "departure_time_constraint":
        # Keyword match: the main token (longest word) in expected must appear in got.
        # Agents rephrase ("morning only" vs "morning flights") but the keyword holds.
        main_word = max(expected.lower().split(), key=len)
        return main_word in got.lower()
    if isinstance(expected, float) and isinstance(got, (int, float)):
        return abs(float(got) - expected) < _FLOAT_TOL
    return expected == got


_WINDOW_BOUND_MAP: dict[str, tuple[str, str]] = {
    "earliest_not_before": ("earliest_departure", ">="),
    "earliest_not_after":  ("earliest_departure", "<="),
    "latest_not_before":   ("latest_departure",   ">="),
    "latest_not_after":    ("latest_departure",   "<="),
}


def _score_window_bounds(
    dw: dict[str, Any],
    intent: TravelIntent,
) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for bound_key, (intent_attr, op) in _WINDOW_BOUND_MAP.items():
        bound_val = dw.get(bound_key)
        if bound_val is None:
            continue
        bound_date = date.fromisoformat(bound_val)
        agent_date: date = getattr(intent, intent_attr)
        results[f"window_{bound_key}"] = (
            agent_date >= bound_date if op == ">=" else agent_date <= bound_date
        )
    return results


def _all_fields_failed(exp: dict[str, Any]) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for f in _REQUIRED_FIELDS:
        if f in exp:
            results[f] = False
    for f in _OPTIONAL_FIELDS:
        if f in exp:
            results[f] = False
    dw = exp.get("departure_window")
    if dw:
        for bound in _WINDOW_BOUND_MAP:
            if dw.get(bound) is not None:
                results[f"window_{bound}"] = False
    return results


def _score_planner_case(case: dict[str, Any], record: dict[str, Any]) -> dict[str, bool]:
    """Return field-level pass/fail for one case."""
    exp = case["expected_planner"]
    intent_d: dict[str, Any] | None = record.get("intent")

    if intent_d is None:
        return _all_fields_failed(exp)

    intent = TravelIntent.model_validate(intent_d)
    field_results: dict[str, bool] = {}

    for f in _REQUIRED_FIELDS:
        if f in exp:
            field_results[f] = intent_d.get(f) == exp[f]

    for f in _OPTIONAL_FIELDS:
        if f in exp:
            field_results[f] = _score_field(f, exp[f], intent_d.get(f))

    dw = exp.get("departure_window")
    if dw:
        field_results.update(_score_window_bounds(dw, intent))

    return field_results


def _check_refine_constraint(constraint: dict[str, Any], filtered: list[FlightOption]) -> bool:
    if not filtered:
        return False
    ctype = constraint.get("type", "")
    if ctype == "direct_only":
        return all(f.layover_count == 0 for f in filtered)
    if ctype == "price_sort":
        prices = [f.price_inr for f in filtered]
        return prices == sorted(prices)
    if ctype == "morning_departure":
        return all(
            _MORNING_START_HOUR <= int(f.outbound_departure_at[11:13]) < _MORNING_END_HOUR
            for f in filtered
        )
    return False


def _score_refine_case(case: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Return {"pass": bool, "reason": str} for a refine constraint check."""
    classified_d: dict[str, Any] | None = record.get("refine_classified")
    if classified_d is None:
        err = record.get("refine_error") or "classification missing"
        return {"pass": False, "reason": f"no classification — {err}"}

    action = classified_d.get("action", "")
    if action != ConversationAction.REFINE:
        return {"pass": False, "reason": f"expected action=refine, got {action!r}"}

    refine_args_d = classified_d.get("refine_args") or {}
    try:
        refine_args = RefineArgs(**refine_args_d)
    except Exception as exc:
        return {"pass": False, "reason": f"RefineArgs parse error: {exc}"}

    filtered = _apply_refine_filters(_SYNTHETIC_REFINE_POOL, refine_args)
    constraint = case["refine"]["constraint"]
    ok = _check_refine_constraint(constraint, filtered)

    if ok:
        return {
            "pass": True,
            "reason": f"{constraint['type']} verified ({len(filtered)} flights after filter)",
        }
    return {
        "pass": False,
        "reason": (
            f"{constraint['type']} NOT satisfied — "
            f"{len(filtered)} flights after filter, args: {refine_args_d}"
        ),
    }


def score_records(
    golden: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score all records against golden. Returns aggregate report dict."""
    record_map = {r["id"]: r for r in records}
    all_field_results: dict[str, list[bool]] = {}
    refine_results: list[dict[str, Any]] = []
    case_failures: list[dict[str, Any]] = []

    for cid, case in golden.items():
        rec = record_map.get(cid)
        if rec is None:
            continue  # not generated yet (--limit run)

        field_results = _score_planner_case(case, rec)
        for fname, ok in field_results.items():
            all_field_results.setdefault(fname, []).append(ok)
            if not ok:
                intent_d = rec.get("intent") or {}
                # Reconstruct expected and got for the failure record
                if fname.startswith("window_"):
                    bound = fname[len("window_"):]
                    dw_exp = case["expected_planner"].get("departure_window") or {}
                    expected_val: Any = dw_exp.get(bound)
                    bound_to_intent = {
                        "earliest_not_before": "earliest_departure",
                        "earliest_not_after": "earliest_departure",
                        "latest_not_before": "latest_departure",
                        "latest_not_after": "latest_departure",
                    }
                    got_val: Any = intent_d.get(bound_to_intent.get(bound, ""))
                else:
                    expected_val = case["expected_planner"].get(fname)
                    got_val = intent_d.get(fname)
                case_failures.append(
                    {"id": cid, "field": fname, "expected": expected_val, "got": got_val}
                )

        if case.get("refine"):
            result = _score_refine_case(case, rec)
            result["id"] = cid
            refine_results.append(result)

    def _acc(fields: list[str]) -> float:
        checks = [v for f in fields for v in all_field_results.get(f, [])]
        return sum(checks) / len(checks) if checks else 1.0

    window_keys = [k for k in all_field_results if k.startswith("window_")]
    n_refine = len(refine_results)
    n_refine_pass = sum(1 for r in refine_results if r.get("pass"))

    return {
        "cases_scored": len(record_map),
        "cases_golden": len(golden),
        "planner": {
            "required_accuracy": round(_acc(list(_REQUIRED_FIELDS)), 3),
            "optional_accuracy": round(_acc(list(_OPTIONAL_FIELDS)), 3),
            "window_accuracy": round(_acc(window_keys), 3),
            "per_field": {
                f: round(sum(v) / len(v), 3) if v else None
                for f, v in sorted(all_field_results.items())
            },
        },
        "refine": {
            "n_cases": n_refine,
            "n_pass": n_refine_pass,
            "pass_rate": round(n_refine_pass / n_refine, 3) if n_refine > 0 else None,
            "results": refine_results,
        },
        "failures": case_failures,
    }


def print_report(report: dict[str, Any], run_path: Path, profile: str) -> None:
    p = report["planner"]
    r = report["refine"]
    sep = "=" * 62

    print(f"\n{sep}")
    print(f"Wave 2 Tier-1 Eval — {profile}")
    print(f"Run:  {run_path.name}")
    print(f"Cases: {report['cases_scored']}/{report['cases_golden']}")
    print(sep)

    print("\nPLANNER ACCURACY")
    print(f"  Required fields:   {p['required_accuracy']:.1%}")
    print(f"  Optional fields:   {p['optional_accuracy']:.1%}")
    print(f"  Departure window:  {p['window_accuracy']:.1%}")
    print(f"\n  {'FIELD':<42} {'ACC':>6}")
    print(f"  {'-'*50}")
    for fname, acc_val in sorted(p["per_field"].items()):
        if acc_val is not None:
            marker = " !" if acc_val < _LOW_ACC_WARN else ""
            print(f"  {fname:<42} {acc_val:>5.1%}{marker}")

    if r["n_cases"] > 0:
        print(f"\nREFINE CONSTRAINTS  {r['n_pass']}/{r['n_cases']} pass", end="")
        if r["pass_rate"] is not None:
            print(f" ({r['pass_rate']:.1%})", end="")
        print()
        for res in r["results"]:
            tag = "PASS" if res.get("pass") else "FAIL"
            print(f"  [{tag}] {res['id']}: {res.get('reason', '')}")

    if report["failures"]:
        print(f"\nFIELD FAILURES ({len(report['failures'])} total, first 20 shown):")
        for fail in report["failures"][:20]:
            print(
                f"  {fail['id']:>10} | {fail['field']:<30} | "
                f"exp={fail['expected']!r:<20} got={fail['got']!r}"
            )

    print(f"\n{sep}")


def write_report(report: dict[str, Any], run_path: Path, profile: str) -> Path:
    _REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    p = report["planner"]
    r = report["refine"]

    lines: list[str] = [
        f"# Wave 2 Tier-1 Eval — {profile}",
        f"\nGenerated: {datetime.now(tz=UTC).isoformat()}",
        f"Run: {run_path.name}",
        f"Cases scored: {report['cases_scored']}/{report['cases_golden']}",
        "\n## Planner accuracy",
        "| Metric | Accuracy |",
        "|---|---|",
        f"| Required fields | {p['required_accuracy']:.1%} |",
        f"| Optional fields | {p['optional_accuracy']:.1%} |",
        f"| Departure window | {p['window_accuracy']:.1%} |",
        "\n### Per-field",
        "| Field | Accuracy |",
        "|---|---|",
    ]
    for fname, acc_val in sorted(p["per_field"].items()):
        if acc_val is not None:
            lines.append(f"| {fname} | {acc_val:.1%} |")

    if r["n_cases"] > 0:
        pass_str = f"{r['n_pass']}/{r['n_cases']}"
        if r["pass_rate"] is not None:
            pass_str += f" ({r['pass_rate']:.1%})"
        lines += [
            "\n## Refine constraints",
            f"**Pass rate: {pass_str}**\n",
        ]
        for res in r["results"]:
            tag = "PASS" if res.get("pass") else "FAIL"
            lines.append(f"- [{tag}] `{res['id']}`: {res.get('reason', '')}")

    if report["failures"]:
        lines += [
            "\n## Field failures",
            "| Case | Field | Expected | Got |",
            "|---|---|---|---|",
        ]
        for fail in report["failures"][:50]:
            exp_s = repr(fail["expected"])
            got_s = repr(fail["got"])
            lines.append(
                f"| `{fail['id']}` | `{fail['field']}` | `{exp_s}` | `{got_s}` |"
            )

    out = _REPORTS_DIR / f"{ts}_{profile}_tier1.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Wave 2 Tier-1 eval scorer")
    parser.add_argument("--run", help="Run JSONL file (default: latest in runs/)")
    parser.add_argument(
        "--threshold", type=float, default=_DEFAULT_THRESHOLD,
        help=f"Required-field accuracy CI gate (default: {_DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--no-fail", action="store_true",
        help="Always exit 0 — report only, don't enforce threshold",
    )
    args = parser.parse_args()

    # Resolve run file
    if args.run:
        p = Path(args.run)
        run_path = p.resolve() if p.is_absolute() or p.exists() else _RUNS_DIR / p.name
    else:
        all_runs = sorted(_RUNS_DIR.glob("*.jsonl"))
        if not all_runs:
            print(
                "No Wave 2 run files found — skipping tier1 eval. "
                "Generate baseline first:\n"
                "  python -m evals.wave2.runner"
            )
            return 0  # don't fail CI before baseline is committed
        run_path = all_runs[-1]

    if not run_path.exists():
        print(f"Run file not found: {run_path}", file=sys.stderr)
        return 2

    golden = _load_golden()
    records = [
        json.loads(line)
        for line in run_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    profile = run_path.stem.split("_", 1)[-1] if "_" in run_path.stem else "unknown"

    report = score_records(golden, records)
    print_report(report, run_path, profile)

    out_path = write_report(report, run_path, profile)
    print(f"Report -> {out_path}")

    if args.no_fail:
        return 0

    req_acc = report["planner"]["required_accuracy"]
    if req_acc < args.threshold:
        print(
            f"\n!! GATE FAIL — required-field accuracy {req_acc:.1%}"
            f" < threshold {args.threshold:.0%}"
        )
        return 1

    print(f"\nGATE PASS — required-field accuracy {req_acc:.1%} >= {args.threshold:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
