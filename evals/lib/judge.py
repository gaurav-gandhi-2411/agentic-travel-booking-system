"""LLM-as-judge pairwise preference evaluator.

Implements double-swap protocol to reduce position bias:
  Round 1: judge(response_A, response_B) → winner
  Round 2: judge(response_B, response_A) → winner
  Score: both A = A preferred; both B = B preferred; split = tie

Judge models: Qwen 2.5 72B primary, Llama 3.3 70B cross-check (both free tier).
No frontier judge in CI — manual eval-baselines only. See ADR-0010.

Full implementation target: Phase 3.5.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Preference(Enum):
    A = "A"
    B = "B"
    TIE = "tie"


@dataclass
class JudgeResult:
    example_id: str
    preference: Preference
    round1_winner: str
    round2_winner: str
    judge_model: str
    rationale: str


def judge_pairwise(
    example_id: str,
    response_a: str,
    response_b: str,
    *,
    agent: str,
    rubric_path: Path,
) -> JudgeResult:
    """Run double-swap pairwise judgement for (response_a, response_b).

    Loads rubric from *rubric_path* (evals/judges/<agent>_judge.md).
    Returns JudgeResult with Preference and per-round winners.
    """
    msg = f"judge.judge_pairwise — implemented in Phase 3.5 (agent={agent!r})"
    raise NotImplementedError(msg)
