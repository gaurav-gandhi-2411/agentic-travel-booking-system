"""Synthetic example generation via OpenRouter teacher model.

Uses Qwen 2.5 72B (free tier, ~50 req/day) to generate (input, expected_output)
pairs seeded by the diversity matrix. Self-critique pass runs after generation.

Full implementation target: Phase 3.5.
"""
from __future__ import annotations

import argparse

from scripts.dataset.diversity_matrix import DESTINATION_REGIONS, iter_seeds

_TEACHER_MODEL = "qwen/qwen-2.5-72b-instruct:free"
_VALID_AGENTS = frozenset(
    {"planner", "flight_hunter", "hotel_hunter", "optimizer", "booking", "conversation"}
)


def generate(agent: str, count: int, *, output_dir: str = "evals/manual/input") -> None:
    """Generate *count* synthetic examples for *agent* using the teacher model.

    Samples from the diversity matrix to ensure coverage, then calls the
    teacher model via OpenRouter free tier. Writes JSONL to *output_dir*.

    Args:
        agent: Agent key from _VALID_AGENTS.
        count: Number of examples to generate.
        output_dir: Directory to write raw JSONL output.
    """
    if agent not in _VALID_AGENTS:
        msg = f"Unknown agent {agent!r}. Valid agents: {sorted(_VALID_AGENTS)}"
        raise ValueError(msg)
    msg = f"generate.generate — implemented in Phase 3.5 (agent={agent!r}, count={count})"
    raise NotImplementedError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic training examples")
    parser.add_argument("--agent", required=True, choices=sorted(_VALID_AGENTS))
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output-dir", default="evals/manual/input")
    args = parser.parse_args()
    generate(args.agent, args.count, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
