"""Ingest human-reviewed examples into the golden dataset.

Reads approved examples from evals/manual/approved/<agent>/ and appends
them to evals/datasets/<agent>.jsonl. Archives ingested source files.

Full implementation target: Phase 3.5.
"""
from __future__ import annotations

import argparse
from pathlib import Path

_VALID_AGENTS = frozenset(
    {"planner", "flight_hunter", "hotel_hunter", "optimizer", "booking", "conversation"}
)
_DEFAULT_APPROVED_DIR = Path("evals/manual/approved")
_DEFAULT_DATASET_DIR = Path("evals/datasets")
_DEFAULT_ARCHIVE_DIR = Path("evals/manual/archived")


def ingest(
    agent: str,
    *,
    approved_dir: Path = _DEFAULT_APPROVED_DIR,
    dataset_dir: Path = _DEFAULT_DATASET_DIR,
    archive_dir: Path = _DEFAULT_ARCHIVE_DIR,
) -> int:
    """Promote human-approved examples for *agent* into the golden dataset.

    Returns the count of examples ingested. Source files are moved to
    *archive_dir* after a successful append to prevent double-ingest.
    """
    if agent not in _VALID_AGENTS:
        msg = f"Unknown agent {agent!r}. Valid agents: {sorted(_VALID_AGENTS)}"
        raise ValueError(msg)
    msg = f"ingest_qa.ingest — implemented in Phase 3.5 (agent={agent!r})"
    raise NotImplementedError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest QA-approved examples into golden dataset")
    parser.add_argument("--agent", required=True, choices=sorted(_VALID_AGENTS))
    args = parser.parse_args()
    count = ingest(args.agent)
    print(f"Ingested {count} examples for agent {args.agent!r}")  # noqa: T201


if __name__ == "__main__":
    main()
