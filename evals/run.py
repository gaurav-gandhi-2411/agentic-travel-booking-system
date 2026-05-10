"""Eval harness CLI entrypoint.

Usage:
    python -m evals.run --agent planner --mode quick
    python -m evals.run --agent all --mode full
    python -m evals.run --agent all --mode baselines

Full implementation target: Phase 3.5.
"""
from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="DealHunter eval runner")
    parser.add_argument(
        "--agent",
        default="all",
        help="Agent to evaluate (planner/flight_hunter/hotel_hunter/optimizer/booking/conversation/all)",
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "full", "baselines"],
        default="quick",
        help="Eval mode: quick (20 examples), full (100%%), baselines (frontier models)",
    )
    parser.add_argument(
        "--output",
        default="evals/results",
        help="Directory to write result JSON",
    )
    args = parser.parse_args()
    msg = f"evals.run — implemented in Phase 3.5 (agent={args.agent!r}, mode={args.mode!r})"
    raise NotImplementedError(msg)


if __name__ == "__main__":
    main()
