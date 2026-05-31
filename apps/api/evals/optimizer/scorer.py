"""Optimizer eval scorer — reads run JSONL, scores, writes markdown report.

Usage:
    python -m evals.optimizer.scorer --run runs/20260516T120000_demo-haiku.jsonl
    python -m evals.optimizer.scorer --all          # score all runs in runs/
    python -m evals.optimizer.scorer                # same as --all
    python -m evals.optimizer.scorer --all --judge-profile eval-judge-qwen3-32b

Scoring:
    label_correct: bool       — archetype labels match deterministic Pareto result
    coherence_avg: float      — mean coherence score (1-5) across all archetypes
    coherence_p50: float      — median coherence score
    coherence_variance: float — variance of scores (higher = less stable)
    high_variance_count: int  — archetypes where judge disagreed > 2 pts across 3 samples
"""

from __future__ import annotations

import argparse
import asyncio
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
    """Score a single run record for completion and label correctness."""
    completed = "error" not in record and bool(record.get("archetypes"))

    if not completed:
        return {**record, "completed": False, "label_correct": None, "coherence": None}

    archetypes = record.get("archetypes", [])
    flights_raw = record.get("flights", [])

    if not flights_raw:
        return {**record, "completed": False, "label_correct": None, "coherence": None}

    expected_val_id, expected_exp_id = _expected_labels(flights_raw)

    got_val_id = next((a["flight"]["id"] for a in archetypes if a["label"] == "best-value"), None)
    got_exp_id = next(
        (a["flight"]["id"] for a in archetypes if a["label"] == "best-experience"), None
    )

    label_correct = got_val_id == expected_val_id and got_exp_id == expected_exp_id
    return {**record, "completed": True, "label_correct": label_correct, "coherence": None}


def score_run_file(path: Path) -> list[dict]:
    """Load and score all records in a JSONL run file."""
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [score_record(r) for r in records]


def _provider_from_model(model: str) -> str:
    """Map a model name to its billing provider."""
    if model.startswith("claude-"):
        return "anthropic"
    # Groq hosts some models under a vendor namespace (e.g. "openai/gpt-oss-120b").
    # Check these before the generic slash check to avoid misrouting them to NIM.
    if model.startswith("openai/"):
        return "groq"
    # NIM-hosted models use vendor-prefixed IDs with a slash (e.g. "qwen/qwen3.5-397b-a17b",
    # "deepseek-ai/deepseek-v4-flash"). Groq models use bare IDs (e.g. "llama-3.3-70b-versatile",
    # "qwen3-32b"). Check slash first so any future NIM Qwen variant routes correctly.
    if "/" in model:
        return "nvidia"
    if model.startswith(("llama", "qwen")):
        return "groq"
    return "unknown"


def _cost_summary(scored: list[dict]) -> dict:
    """Aggregate per-provider spend and call counts across all completed records."""
    spend: dict[str, float] = {"anthropic": 0.0, "groq": 0.0, "nvidia": 0.0}
    calls: dict[str, int] = {"anthropic": 0, "groq": 0, "nvidia": 0}
    calls_per_scenario = 3  # 2 explain + 1 compare

    for rec in scored:
        if not rec.get("completed"):
            continue
        model = rec.get("model", "")
        provider = _provider_from_model(model)
        if provider in calls:
            calls[provider] += calls_per_scenario
            spend[provider] += rec.get("cost_usd_estimate", 0.0)

    return {"spend": spend, "calls": calls}


def _format_provider_spend(label: str, usd: float, n_calls: int, *, free_tier: bool) -> str:
    """Return the spend line for one provider."""
    if n_calls == 0:
        return f"  {label}: $0 (no calls)"
    if free_tier:
        return f"  {label}: $0 ({n_calls} calls, free tier)"
    # Paid provider: distinguish tracked spend from missing cost_usd_estimate field
    if usd > 0:
        return f"  !! {label}: ${usd:.5f} ({n_calls} calls)"
    return f"  {label}: not tracked in this run ({n_calls} calls)"


def _print_cost_summary(scored: list[dict]) -> None:
    cs = _cost_summary(scored)
    print(
        _format_provider_spend(
            "Anthropic spend this run",
            cs["spend"]["anthropic"],
            cs["calls"]["anthropic"],
            free_tier=False,
        )
    )
    print(
        _format_provider_spend(
            "Groq spend this run",
            cs["spend"]["groq"],
            cs["calls"]["groq"],
            free_tier=True,
        )
    )
    print(
        _format_provider_spend(
            "NVIDIA NIM spend this run",
            cs["spend"]["nvidia"],
            cs["calls"]["nvidia"],
            free_tier=True,
        )
    )


def _coherence_summary(scored: list[dict]) -> dict:
    """Aggregate coherence metrics across all scored records."""
    all_coh: list[int] = []
    hv_count = 0
    for rec in scored:
        for js in rec.get("judge_scores", []):
            s = js.get("coherence_score")
            if s is not None:
                all_coh.append(int(s))
            if js.get("high_variance"):
                hv_count += 1

    if not all_coh:
        return {
            "coherence_avg": None,
            "coherence_p50": None,
            "coherence_variance": None,
            "high_variance_count": 0,
        }
    return {
        "coherence_avg": round(statistics.mean(all_coh), 3),
        "coherence_p50": round(statistics.median(all_coh), 1),
        "coherence_variance": round(statistics.variance(all_coh) if len(all_coh) > 1 else 0.0, 3),
        "high_variance_count": hv_count,
    }


def _cache_summary(scored: list[dict]) -> dict:
    """Aggregate cache token counts across all completed records."""
    total_read = sum(
        (rec.get("cache_read_tokens") or 0) for rec in scored if rec.get("completed")
    )
    total_write = sum(
        (rec.get("cache_write_tokens") or 0) for rec in scored if rec.get("completed")
    )
    total_input = sum(
        (rec.get("input_tokens_actual") or 0) for rec in scored if rec.get("completed")
    )
    standard_input = max(0, total_input - total_read - total_write)
    denom = total_read + standard_input
    hit_rate = round(total_read / denom, 3) if denom > 0 else 0.0
    return {
        "cache_read_tokens": total_read,
        "cache_write_tokens": total_write,
        "cache_hit_rate": hit_rate,
    }


def _judge_model_summary(scored: list[dict]) -> dict:
    """Return judge model stats for a set of scored records.

    Returns: {
        "judge_models": sorted list of distinct non-empty judge_model values seen,
        "unknown_count": number of judge_score entries with judge_model == "",
        "mixed": True if more than one distinct non-empty judge_model is present,
    }
    """
    models: set[str] = set()
    unknown_count = 0
    for rec in scored:
        for js in rec.get("judge_scores", []):
            jm = js.get("judge_model", "")
            if jm:
                models.add(jm)
            else:
                unknown_count += 1
    return {
        "judge_models": sorted(models),
        "unknown_count": unknown_count,
        "mixed": len(models) > 1,
    }


def check_cross_profile_judge_consistency(summaries: list[dict]) -> tuple[bool, str]:
    """Check whether cross-profile coherence comparison is valid.

    Returns (ok, message).
    ok=True means all profiles used the same single known judge — comparison is valid.
    ok=False means judges differ or are unknown — cross-profile comparison is not valid.

    Rules:
    - If no summary has coherence data, return True (no comparison to gate).
    - If any summary has judge_mixed=True, refuse (mixed judges within a single run).
    - Collect the set of distinct known judge_models across all summaries.
      - If the set is empty (all legacy/unknown), refuse.
      - If the set has more than 1 entry, refuse.
      - If any summary has unknown_count > 0 and the set has 1 entry, warn but allow.
      - If the set has exactly 1 entry and unknown_count == 0 everywhere, allow.
    """
    coherence_summaries = [s for s in summaries if s.get("coherence_avg") is not None]
    if not coherence_summaries:
        return True, ""

    # Any within-run mix is an immediate fail
    if any(s.get("judge_mixed") for s in coherence_summaries):
        mixed_profiles = [s["profile"] for s in coherence_summaries if s.get("judge_mixed")]
        return False, (
            f"Mixed judges detected within run(s): {mixed_profiles}. "
            "Coherence scores within these profiles are not internally comparable."
        )

    # Collect distinct known judge models across all profiles
    all_models: set[str] = set()
    total_unknown = sum(s.get("judge_unknown_count", 0) for s in coherence_summaries)
    for s in coherence_summaries:
        all_models.update(s.get("judge_models", []))

    if len(all_models) == 0:
        return False, (
            "All coherence entries have unknown/legacy judge attribution. "
            "Re-run scoring to populate judge_model and enable cross-profile comparison."
        )

    if len(all_models) > 1:
        return False, (
            f"Cross-profile judge mismatch: profiles used different judges "
            f"({', '.join(sorted(all_models))}). "
            "Coherence deltas between profiles are not comparable."
        )

    # Exactly one known judge model
    if total_unknown > 0:
        judge = next(iter(all_models))
        return True, (
            f"Warning: {total_unknown} legacy/unknown judge entries mixed with {judge} entries. "
            "Cross-profile comparison is provisionally valid but legacy entries may differ."
        )

    return True, ""


def print_summary(scored: list[dict], profile: str) -> dict:
    """Print per-profile summary and return summary dict."""
    total = len(scored)
    completed = sum(1 for r in scored if r.get("completed"))
    # label_correct is None for incomplete records; only count completed ones
    label_correct_on_completed = sum(
        1 for r in scored if r.get("completed") and r.get("label_correct")
    )
    latencies = [r["latency_ms"] for r in scored if "latency_ms" in r and r.get("completed")]
    p50 = round(statistics.median(latencies), 0) if latencies else 0
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    p95 = round(sorted(latencies)[p95_idx], 0) if latencies else 0

    completion_pct = round(100 * completed / total, 1) if total else 0.0
    label_pct = 100 * label_correct_on_completed // completed if completed else 0
    coh = _coherence_summary(scored)

    print(f"\n### {profile}")
    print(
        f"  Completion: {completed}/{total} ({completion_pct}%)  "
        f"Label-correct (completed): {label_correct_on_completed}/{completed} ({label_pct}%)"
    )
    print(f"  Latency p50: {p50}ms  p95: {p95}ms")
    _print_cost_summary(scored)
    cache = _cache_summary(scored)
    print(
        f"  Cache: {cache['cache_write_tokens']:,} writes / {cache['cache_read_tokens']:,} reads"
        f"  |  hit rate: {cache['cache_hit_rate']:.1%}"
    )
    if coh["coherence_avg"] is not None:
        print(
            f"  Coherence avg: {coh['coherence_avg']}  "
            f"p50: {coh['coherence_p50']}  "
            f"variance: {coh['coherence_variance']}  "
            f"high-variance archetypes: {coh['high_variance_count']}"
        )

    jm = _judge_model_summary(scored)
    if jm["judge_models"] or jm["unknown_count"] > 0:
        if jm["mixed"]:
            print(
                f"  ⚠  Mixed judges in this run: {', '.join(jm['judge_models'])} "
                f"— coherence scores are not internally comparable"
            )
        elif jm["judge_models"]:
            print(f"  Judge: {jm['judge_models'][0]}", end="")
            if jm["unknown_count"] > 0:
                print(f" (+{jm['unknown_count']} legacy/unknown entries)", end="")
            print()
        else:
            print(f"  Judge: unknown/legacy ({jm['unknown_count']} entries)")

    return {
        "profile": profile,
        "total": total,
        "completed": completed,
        "completion_pct": completion_pct,
        "label_correct_on_completed": label_correct_on_completed,
        "label_correct_pct": label_pct,
        "latency_p50": p50,
        "latency_p95": p95,
        **coh,
        "cache_read_tokens": cache["cache_read_tokens"],
        "cache_write_tokens": cache["cache_write_tokens"],
        "cache_hit_rate": cache["cache_hit_rate"],
        "judge_models": jm["judge_models"],
        "judge_unknown_count": jm["unknown_count"],
        "judge_mixed": jm["mixed"],
    }


def write_report(  # noqa: PLR0912, PLR0915
    summaries: list[dict],
    out_path: Path,
    scored_by_profile: dict[str, list[dict]] | None = None,
) -> None:
    """Write a markdown report to out_path."""
    _REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).isoformat()
    has_coherence = any(s.get("coherence_avg") is not None for s in summaries)

    cross_ok, cross_msg = check_cross_profile_judge_consistency(summaries)
    if not cross_ok:
        print(f"\n!! CROSS-PROFILE JUDGE GATE: {cross_msg}")
    elif cross_msg:
        print(f"\n  Cross-profile note: {cross_msg}")

    has_cache = any(
        s.get("cache_read_tokens", 0) > 0 or s.get("cache_write_tokens", 0) > 0
        for s in summaries
    )
    if has_coherence:
        header = (
            "| Profile | Completion | Label % (completed)"
            " | Coh avg | Coh p50 | Coh var | HV count"
            " | Lat p50 | Lat p95"
            + (" | Cache writes | Cache reads | Hit rate" if has_cache else "")
            + " |"
        )
        sep = "|---|---|---|---|---|---|---|---|---|" + ("|---|---|---|" if has_cache else "")
    else:
        header = (
            "| Profile | Completion | Label % (completed) | Latency p50 | Latency p95"
            + (" | Cache writes | Cache reads | Hit rate" if has_cache else "")
            + " |"
        )
        sep = "|---|---|---|---|---|" + ("|---|---|---|" if has_cache else "")

    lines: list[str] = [
        "# Optimizer Eval Report\n",
        f"Generated: {ts}\n",
    ]
    if not cross_ok:
        lines.append(
            f"⚠  **Cross-profile coherence comparison INVALID**: {cross_msg}\n"
        )
    elif cross_msg:
        lines.append(f"[i] *Cross-profile note*: {cross_msg}\n")
    lines += [
        header,
        sep,
    ]
    for s in summaries:
        comp = f"{s['completed']}/{s['total']} ({s['completion_pct']}%)"
        lbl = f"{s['label_correct_on_completed']}/{s['completed']} ({s['label_correct_pct']}%)"
        if has_coherence:
            coh_avg = s.get("coherence_avg", "—")
            coh_p50 = s.get("coherence_p50", "—")
            coh_var = s.get("coherence_variance", "—")
            hv = s.get("high_variance_count", 0)
            cache_cols = (
                f" | {s.get('cache_write_tokens', 0):,}"
                f" | {s.get('cache_read_tokens', 0):,}"
                f" | {s.get('cache_hit_rate', 0):.1%}"
                if has_cache
                else ""
            )
            lines.append(
                f"| {s['profile']} | {comp} | {lbl}"
                f" | {coh_avg} | {coh_p50} | {coh_var} | {hv}"
                f" | {s['latency_p50']}ms | {s['latency_p95']}ms"
                + cache_cols
                + " |"
            )
        else:
            cache_cols = (
                f" | {s.get('cache_write_tokens', 0):,}"
                f" | {s.get('cache_read_tokens', 0):,}"
                f" | {s.get('cache_hit_rate', 0):.1%}"
                if has_cache
                else ""
            )
            lines.append(
                "| " + s["profile"] + " | " + comp + " | " + lbl
                + f" | {s['latency_p50']}ms | {s['latency_p95']}ms"
                + cache_cols
                + " |"
            )

    # Per-profile detail sections
    if scored_by_profile:
        for profile, scored in scored_by_profile.items():
            lines.append(f"\n## {profile}\n")

            # Runner failures
            failures = [r for r in scored if not r.get("completed")]
            lines.append("### Runner failures (no archetypes produced)")
            if failures:
                lines.append("| Scenario | Route | Error |")
                lines.append("|---|---|---|")
                for r in failures:
                    flights = r.get("flights", [])
                    if flights:
                        orig = flights[0].get("origin_iata", "?")
                        dest = flights[0].get("destination_iata", "?")
                        route = f"{orig}→{dest}"
                    else:
                        route = "?"
                    err = str(r.get("error", "unknown"))
                    # Summarise to fit a table cell
                    if "tokens per minute" in err:
                        err_short = "429 TPM"
                    elif "tokens per day" in err:
                        err_short = "429 TPD"
                    elif "tokens per second" in err:
                        err_short = "429 TPS"
                    else:
                        err_short = err[:60]
                    lines.append(f"| {r.get('id', '?')} | {route} | {err_short} |")
            else:
                lines.append("*(none)*")

            # Label mismatches
            mismatches = [
                r for r in scored if r.get("completed") and r.get("label_correct") is False
            ]
            lines.append("\n### Label mismatches (archetypes produced, wrong label selected)")
            if mismatches:
                lines.append(
                    "| Scenario | Expected value-id | Got value-id | Expected exp-id | Got exp-id |"
                )
                lines.append("|---|---|---|---|---|")
                for r in mismatches:
                    lines.append(
                        f"| {r.get('id', '?')} | (see JSONL) | (see JSONL)"
                        " | (see JSONL) | (see JSONL) |"
                    )
            else:
                lines.append("*(none)*")

            # High-variance archetypes
            hv_rows = []
            archetypes = []  # noqa: F841
            for r in scored:
                for i, js in enumerate(r.get("judge_scores", [])):
                    if js.get("high_variance"):
                        arch_list = r.get("archetypes") or []
                        arch = arch_list[i] if i < len(arch_list) else {}
                        hv_rows.append((r.get("id", "?"), arch.get("label", "?"), js["all_scores"]))
            lines.append("\n### High-variance archetypes (score range > 2 across 3 judge samples)")
            if hv_rows:
                lines.append("| Scenario | Label | Scores |")
                lines.append("|---|---|---|")
                for sid, lbl_name, scores in hv_rows:
                    lines.append(f"| {sid} | {lbl_name} | {scores} |")
            else:
                lines.append("*(none)*")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written -> {out_path}")


async def _run_coherence(scored: list[dict], judge_profile: str) -> list[dict]:
    from evals.optimizer.judge import CoherenceJudge, score_all_archetypes  # noqa: PLC0415

    judge = CoherenceJudge(judge_profile=judge_profile)
    return await score_all_archetypes(scored, judge)


def _check_gates(summaries: list[dict]) -> bool:
    """Return True if every profile clears all quality thresholds; print violations."""
    from evals.optimizer.thresholds import (  # noqa: PLC0415
        THRESHOLD_COHERENCE_MIN,
        THRESHOLD_COMPLETION_MIN,
        THRESHOLD_HIGH_VARIANCE_MAX_PCT,
        THRESHOLD_LABEL_CORRECT_COMPLETED,
    )

    violations: list[str] = []
    for s in summaries:
        p = s["profile"]
        total = s["total"] or 1
        completion = s["completed"] / total
        if completion < THRESHOLD_COMPLETION_MIN:
            violations.append(f"{p}: completion {completion:.3f} < {THRESHOLD_COMPLETION_MIN}")
        if s["completed"]:
            label_rate = s["label_correct_on_completed"] / s["completed"]
            if label_rate < THRESHOLD_LABEL_CORRECT_COMPLETED:
                violations.append(
                    f"{p}: label_correct {label_rate:.3f} < {THRESHOLD_LABEL_CORRECT_COMPLETED}"
                )
        if s.get("coherence_avg") is not None and s["coherence_avg"] < THRESHOLD_COHERENCE_MIN:
            violations.append(
                f"{p}: coherence_avg {s['coherence_avg']} < {THRESHOLD_COHERENCE_MIN}"
            )
        if s.get("high_variance_count") is not None and s["completed"]:
            hv_pct = s["high_variance_count"] / (s["completed"] * 2)
            if hv_pct > THRESHOLD_HIGH_VARIANCE_MAX_PCT:
                violations.append(
                    f"{p}: high_variance_pct {hv_pct:.3f} > {THRESHOLD_HIGH_VARIANCE_MAX_PCT}"
                )

    if violations:
        print("\n!! GATE VIOLATIONS — eval did not pass thresholds:")
        for v in violations:
            print(f"  {v}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimizer eval scorer")
    parser.add_argument("--run", help="Path to a single run JSONL file")
    parser.add_argument("--all", action="store_true", help="Score all run files")
    parser.add_argument(
        "--judge-profile",
        default=None,
        help="Judge profile name (e.g. eval-judge-qwen3-32b). Omit to skip coherence.",
    )
    args = parser.parse_args()

    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    paths = [Path(args.run)] if args.run else sorted(_RUNS_DIR.glob("*.jsonl"))

    if not paths:
        print("No run files found. Run runner.py first.")
        return 1

    summaries = []
    scored_by_profile: dict[str, list[dict]] = {}
    for path in paths:
        stem = path.stem
        profile = stem.split("_", 1)[-1] if "_" in stem else stem
        scored = score_run_file(path)

        if args.judge_profile:
            scored = asyncio.run(_run_coherence(scored, args.judge_profile))

        summaries.append(print_summary(scored, profile))
        scored_by_profile[profile] = scored

    report_path = _REPORTS_DIR / f"{ts}_report.md"
    write_report(summaries, report_path, scored_by_profile=scored_by_profile)
    cross_ok, cross_msg = check_cross_profile_judge_consistency(summaries)
    if not cross_ok:
        print(f"\n!! CROSS-PROFILE JUDGE GATE FAILED: {cross_msg}")
    return 0 if _check_gates(summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
