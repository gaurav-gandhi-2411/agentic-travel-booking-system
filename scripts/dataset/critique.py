"""Self-critique chain for generated dataset examples.

Each generated example is run through a critic prompt using the same teacher
model (Qwen 2.5 72B). Examples that fail quality checks are regenerated up to
two times before being written to the rejected queue.

Quality criteria:
- Instruction following: expected output satisfies all constraints in the input
- Format compliance: output matches the target schema for the agent
- Factual plausibility: no obviously impossible routes/dates/hotels

Full implementation target: Phase 3.5.
"""
from __future__ import annotations

import argparse
from pathlib import Path

_MAX_RETRIES = 2
_VALID_AGENTS = frozenset(
    {"planner", "flight_hunter", "hotel_hunter", "optimizer", "booking", "conversation"}
)


def critique_batch(
    agent: str,
    input_dir: Path,
    *,
    output_dir: Path | None = None,
    rejected_dir: Path | None = None,
) -> tuple[int, int]:
    """Run the self-critique chain on all examples in *input_dir* for *agent*.

    Returns (passed_count, rejected_count). Passed examples written to
    *output_dir* (default: same as input). Rejected written to *rejected_dir*.

    Regeneration uses the same teacher model up to _MAX_RETRIES times.
    """
    msg = f"critique.critique_batch — implemented in Phase 3.5 (agent={agent!r})"
    raise NotImplementedError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-critique pass on generated examples")
    parser.add_argument("--agent", required=True, choices=sorted(_VALID_AGENTS))
    parser.add_argument("--input-dir", default="evals/manual/input")
    args = parser.parse_args()
    critique_batch(args.agent, Path(args.input_dir))


if __name__ == "__main__":
    main()
