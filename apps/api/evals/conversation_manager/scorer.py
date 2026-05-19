"""Conversation manager eval scorer — reads run JSONL, scores, writes markdown report.

Usage:
    python -m evals.conversation_manager.scorer --run runs/20260519T120000_demo-llama.jsonl
    python -m evals.conversation_manager.scorer --all          # score all runs in runs/
    python -m evals.conversation_manager.scorer                # same as --all

Scoring:
    action_correct: bool   — actual action matches expected (REFINE/REPLAN/NO_OP)
    args_match: bool       — expected arg fields present and matching in actual output (lenient)
    latency_p50/p95: ms    — percentile latencies across scorable scenarios
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


def _check_args(record: dict) -> bool:
    """Lenient args check: all expected arg keys must match values in actual output.

    Leniency: extra fields in the actual output are ignored. Only the fields
    explicitly listed in expected_args must match (e.g. expected sort_by="price"
    allows any other RefineArgs fields to take their defaults).
    NO_OP scenarios have empty expected_args and always return True here.
    """
    expected_args = record.get("expected_args") or {}
    actual_output = record.get("actual_output") or {}

    if not expected_args:
        return True  # no_op — no structured args to validate

    for block_key in ("refine_args", "replan_args"):
        if block_key not in expected_args:
            continue
        expected_block = expected_args[block_key]
        actual_block = actual_output.get(block_key) or {}
        for key, val in expected_block.items():
            if actual_block.get(key) != val:
                return False

    return True


def score_record(record: dict) -> dict:
    """Score one run record for action correctness and args match."""
    if record.get("dry_run") or record.get("error") or record.get("actual_output") is None:
        return {**record, "scorable": False, "action_correct": None, "args_match": None}

    expected = record["expected_action"]
    actual_action = (record.get("actual_output") or {}).get("action")
    action_correct = actual_action == expected
    args_match = _check_args(record) if action_correct else False

    return {**record, "scorable": True, "action_correct": action_correct, "args_match": args_match}


def score_run_file(path: Path) -> list[dict]:
    """Load and score all records in a JSONL run file."""
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return [score_record(r) for r in records]


def print_summary(scored: list[dict], profile: str) -> dict:
    """Print per-profile summary and return summary dict."""
    scorable = [r for r in scored if r.get("scorable")]
    total = len(scorable)

    n_action_correct = sum(1 for r in scorable if r.get("action_correct"))
    n_args_match = sum(1 for r in scorable if r.get("args_match"))

    latencies = [r["latency_ms"] for r in scorable if r.get("latency_ms") is not None]
    p50 = round(statistics.median(latencies), 0) if latencies else 0
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    p95 = round(sorted(latencies)[p95_idx], 0) if latencies else 0

    action_acc = round(n_action_correct / total, 3) if total else 0.0
    args_acc = round(n_args_match / n_action_correct, 3) if n_action_correct else 0.0

    by_cat: dict[str, tuple[int, int]] = {}
    for r in scorable:
        cat = r.get("category", "?")
        n_correct, n_total = by_cat.get(cat, (0, 0))
        by_cat[cat] = (n_correct + (1 if r.get("action_correct") else 0), n_total + 1)

    print(f"\n### {profile}")
    if total == 0:
        print("  No scorable records (dry-run or all errors)")
    else:
        print(f"  Action accuracy: {n_action_correct}/{total} ({action_acc:.1%})")
        print(
            f"  Args match (when action correct): "
            f"{n_args_match}/{n_action_correct} ({args_acc:.1%})"
        )
        print(f"  Latency p50: {p50}ms  p95: {p95}ms")
        for cat, (c, t) in sorted(by_cat.items()):
            print(f"  [{cat}] {c}/{t}")

    return {
        "profile": profile,
        "total": total,
        "action_correct": n_action_correct,
        "action_accuracy": action_acc,
        "args_match": n_args_match,
        "args_accuracy": args_acc,
        "latency_p50": p50,
        "latency_p95": p95,
        "by_category": {cat: {"correct": c, "total": t} for cat, (c, t) in by_cat.items()},
    }


def write_report(summaries: list[dict], out_path: Path) -> None:
    """Write a markdown report to out_path."""
    _REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).isoformat()

    lines = [
        "# Conversation Manager Eval Report\n",
        f"Generated: {ts}\n",
        "| Profile | Action acc | Args acc | Lat p50 | Lat p95 |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        if s["total"] == 0:
            lines.append(f"| {s['profile']} | — | — | — | — |")
        else:
            lines.append(
                f"| {s['profile']} "
                f"| {s['action_correct']}/{s['total']} ({s['action_accuracy']:.1%}) "
                f"| {s['args_match']}/{s['action_correct']} ({s['args_accuracy']:.1%}) "
                f"| {s['latency_p50']}ms "
                f"| {s['latency_p95']}ms |"
            )

    for s in summaries:
        if s["total"] == 0:
            continue
        lines.append(f"\n## {s['profile']}\n")
        lines.append("### Per-category accuracy\n")
        lines.append("| Category | Correct | Total |")
        lines.append("|---|---|---|")
        for cat, vals in sorted(s.get("by_category", {}).items()):
            lines.append(f"| {cat} | {vals['correct']} | {vals['total']} |")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written -> {out_path}")


def _check_gates(summaries: list[dict]) -> bool:
    """Return True if every scorable profile clears all quality thresholds."""
    from evals.conversation_manager.thresholds import (  # noqa: PLC0415
        CONVERSATION_ACTION_ACCURACY_MIN,
        CONVERSATION_LATENCY_P95_MAX_MS,
        CONVERSATION_LATENCY_P95_WARN_MS,
    )

    violations: list[str] = []
    warnings: list[str] = []

    for s in summaries:
        if s["total"] == 0:
            continue  # dry-run or all-error profile — skip gates
        p = s["profile"]
        if s["action_accuracy"] < CONVERSATION_ACTION_ACCURACY_MIN:
            threshold = CONVERSATION_ACTION_ACCURACY_MIN
            violations.append(
                f"{p}: action_accuracy {s['action_accuracy']:.3f} < {threshold}"
            )
        p95 = s["latency_p95"]
        if p95 > CONVERSATION_LATENCY_P95_MAX_MS:
            violations.append(
                f"{p}: latency_p95 {p95}ms > {CONVERSATION_LATENCY_P95_MAX_MS}ms (hard fail)"
            )
        elif p95 > CONVERSATION_LATENCY_P95_WARN_MS:
            warnings.append(
                f"{p}: latency_p95 {p95}ms > {CONVERSATION_LATENCY_P95_WARN_MS}ms (warn)"
            )

    if warnings:
        print("\n[WARN] Latency thresholds:")
        for w in warnings:
            print(f"  {w}")
    if violations:
        print("\n!! GATE VIOLATIONS — eval did not pass thresholds:")
        for v in violations:
            print(f"  {v}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Conversation manager eval scorer")
    parser.add_argument("--run", help="Path to a single run JSONL file")
    parser.add_argument("--all", action="store_true", help="Score all run files")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Score only the latest run per profile (default when no flags given)",
    )
    args = parser.parse_args()

    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    all_paths = sorted(_RUNS_DIR.glob("*.jsonl"))

    if args.run:
        paths = [Path(args.run)]
    elif args.all:
        paths = all_paths
    else:
        # Default: latest run per profile (by ISO-timestamp filename sort)
        profile_latest: dict[str, Path] = {}
        for path in all_paths:
            stem = path.stem
            profile = stem.split("_", 1)[-1] if "_" in stem else stem
            profile_latest[profile] = path
        paths = list(profile_latest.values())

    if not paths:
        print("No run files found. Run runner.py first.")
        return 1

    summaries = []
    for path in paths:
        stem = path.stem
        profile = stem.split("_", 1)[-1] if "_" in stem else stem
        scored = score_run_file(path)
        summaries.append(print_summary(scored, profile))

    report_path = _REPORTS_DIR / f"{ts}_report.md"
    write_report(summaries, report_path)
    return 0 if _check_gates(summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
