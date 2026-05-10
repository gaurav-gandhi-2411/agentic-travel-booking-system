# Eval Harness

Evaluation framework for DealHunter agents against golden datasets.

## Overview

| Target | Examples | Runtime | Trigger |
|--------|----------|---------|---------|
| `make eval-quick` | 20 per agent | ~2 min | PRs, local dev |
| `make eval-full` | 100% of dataset | ~30 min | Nightly CI |
| `make eval-baselines` | 100% (frontier) | ~60 min | Manual only |

See ADR-0010 for harness design and ADR-0009 for per-agent pass thresholds.

## Structure

```
evals/
├── datasets/        Golden datasets per agent (JSONL)
├── judges/          Judge prompts + scoring rubrics
├── lib/             Core harness library
│   ├── runner.py    Batch inference + timing
│   ├── scorer.py    Metric aggregation
│   └── judge.py     LLM-as-judge pairwise eval
├── manual/          Human QA input queue and reviewed outputs
├── results/         Eval run outputs (gitignored except summaries)
├── tests/           Unit tests for harness internals
└── run.py           CLI entrypoint
```

## Running Evals

```bash
# Quick smoke check (20 examples per agent, ~2 min)
make eval-quick

# Full dataset eval (nightly)
make eval-full

# Frontier baseline (requires ANTHROPIC_API_KEY, LLM_ROUTING_PROFILE=eval)
make eval-baselines
```

## Adding a Golden Example

1. Add a JSONL row to `datasets/<agent>.jsonl` following the schema in `datasets/README.md`.
2. Run `make eval-quick` locally to confirm it passes.
3. Commit dataset and results summary together.

## Regression Policy

A >2% drop on any metric blocks merge (enforced in CI via `make eval-full`).
