"""Batch inference runner with per-call latency tracking.

Loads examples from evals/datasets/<agent>.jsonl, calls the agent,
and records (output, latency_ms) per example.

Full implementation target: Phase 3.5.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RunResult:
    example_id: str
    agent: str
    model: str
    output: str
    latency_ms: float
    metadata: dict[str, Any]


def run_agent_on_dataset(
    agent: str,
    dataset_path: Path,
    *,
    limit: int | None = None,
) -> list[RunResult]:
    """Run *agent* on every example in *dataset_path*, returning results.

    Args:
        agent: Agent key (planner, flight_hunter, etc.)
        dataset_path: Path to agent JSONL dataset file.
        limit: Cap examples (None = all). Used by eval-quick (limit=20).
    """
    msg = f"runner.run_agent_on_dataset — implemented in Phase 3.5 (agent={agent!r})"
    raise NotImplementedError(msg)
