"""Optimizer eval scorer — reads run JSONL, scores, writes markdown report.

Usage:
    python -m evals.optimizer.scorer --run runs/20260516T120000_demo-haiku.jsonl
    python -m evals.optimizer.scorer --all          # score all runs in runs/
    python -m evals.optimizer.scorer                # same as --all

Scoring:
    label_correct: bool   — archetype labels match expected Pareto result
    coherence: None       — deferred to Phase 2c (LLM-as-judge)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

_RUNS_DIR = Path(__file__).parent / "runs"
_REPORTS_DIR = Path(__file__).parent / "reports"


def _expected_labels(flights_raw: list[dict]) -> tuple[str, str]:
    """Return (expected_value_id, expected_exp_id) from deterministic Pareto."""
    from travel_agent.coordinator.state import FlightOption  # noqa: PLC0415
    from travel_agent.utility.experience import experience_score  # noqa: PLC0415
    from travel_agent.utility.pareto import pareto_frontier  # noqa: PLC0415
    from travel_agent.utility.value import value_score  # noqa: PLC0415

    flights = [FlightOption.model_validate(f) for f in flights_raw]
    frontier = pareto_frontier(flights, value_score, experience_score) or flights
    best_value = max(frontier, key=value_score)
    best_exp = max(frontier, key=experience_score)
    return best_value.id, best_exp.id


def score_record(record: dict) -> dict:
    """Score a single run record."""
    if "error" in record:
        return {**record, "label_correct": False, "coherence": None}

    archetypes = record.get("archetypes", [])
    flights_raw = record.get("flights", [])

    if not archetypes or not flights_raw:
        return {**record, "label_correct": False, "coherence": None}

    expected_val_id, expected_exp_id = _expected_labels(flights_raw)

    # Find what the optimizer actually picked
    got_val_id = next(
        (a["flight"]["id"] for a in archetypes if a["label"] == "best-value"), None
    )
    got_exp_id = next(
        (a["flight"]["id"] for a in archetypes if a["label"] == "best-experience"), None
    )

    label_correct = got_val_id == expected_val_id and got_exp_id == expected_exp_id

    return {**record, "label_correct": label_correct, "coherence": None}


def score_run_file(path: Path) -> list[dict]:
    """Load and score all records in a JSONL run file."""
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [score_record(r) for r in records]


def print_summary(scored: list[dict], profile: str) -> dict:
    """Print per-profile summary and return summary dict."""
    total = len(scored)
    correct = sum(1 for r in scored if r.get("label_correct"))
    latencies = [r["latency_ms"] for r in scored if "latency_ms" in r]
    p50 = round(statistics.median(latencies), 0) if latencies else 0
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    p95 = round(sorted(latencies)[p95_idx], 0) if latencies else 0

    pct = 100 * correct // total if total else 0
    print(f"\n### {profile}")
    print(f"  Label correct: {correct}/{total} ({pct}%)")
    print(f"  Latency p50: {p50}ms  p95: {p95}ms")
    return {
        "profile": profile,
        "total": total,
        "label_correct": correct,
        "label_correct_pct": pct,
        "latency_p50": p50,
        "latency_p95": p95,
    }


def write_report(summaries: list[dict], out_path: Path) -> None:
    """Write a markdown report to out_path."""
    _REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).isoformat()
    lines = [
        "# Optimizer Eval Report\n",
        f"Generated: {ts}\n",
        "| Profile | Label Correct | Label Correct % | Latency p50 | Latency p95 |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['profile']} | {s['label_correct']}/{s['total']} "
            f"| {s['label_correct_pct']}% "
            f"| {s['latency_p50']}ms | {s['latency_p95']}ms |"
        )
    out_path.write_text("\n".join(lines) + "\n")
    print(f"\nReport written -> {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimizer eval scorer")
    parser.add_argument("--run", help="Path to a single run JSONL file")
    parser.add_argument("--all", action="store_true", help="Score all run files")
    args = parser.parse_args()

    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")

    paths = [Path(args.run)] if args.run else sorted(_RUNS_DIR.glob("*.jsonl"))

    if not paths:
        print("No run files found. Run runner.py first.")
        return 1

    summaries = []
    for path in paths:
        # Extract profile from filename: <timestamp>_<profile>.jsonl
        stem = path.stem
        profile = stem.split("_", 1)[-1] if "_" in stem else stem
        scored = score_run_file(path)
        summaries.append(print_summary(scored, profile))

    report_path = _REPORTS_DIR / f"{ts}_report.md"
    write_report(summaries, report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
