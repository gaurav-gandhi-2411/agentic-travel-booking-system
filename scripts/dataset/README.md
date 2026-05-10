# Dataset Generation Pipeline

Synthetic training data generation for DealHunter agent fine-tuning.

## Pipeline Overview

```
diversity_matrix.py  →  generate.py  →  critique.py  →  ingest_qa.py
     (seeds)            (OpenRouter)      (self-critique)   (human QA)
```

1. **`diversity_matrix.py`**: Defines the combination space — destinations × traveler profiles × budget tiers × ambiguity levels. Seeds generation to ensure coverage.
2. **`generate.py`**: Calls the teacher model (Qwen 2.5 72B via OpenRouter free) to generate (input, expected_output) pairs using seed combinations.
3. **`critique.py`**: Self-critique chain — runs each generated example through a critic prompt; flags and regenerates if quality threshold not met.
4. **`ingest_qa.py`**: Moves human-approved examples from `evals/manual/approved/` into `evals/datasets/<agent>.jsonl`.

## Cost Model

| Stage | Model | Cost |
|-------|-------|------|
| generate | Qwen 2.5 72B (OpenRouter free) | $0 (~50 req/day limit) |
| critique | Qwen 2.5 72B (OpenRouter free) | $0 |
| human QA | claude.ai manual session | $0 (subscription) |

Full pipeline: ~$0 API spend, 15–25 h human time. See ADR-0011.

## Targets

1,000 training + 100 eval examples per agent × 6 agents = 6,600 total.

## Running

```bash
# Generate examples for planner (Phase 3.5)
python -m scripts.dataset.generate --agent planner --count 100

# Run self-critique pass
python -m scripts.dataset.critique --agent planner

# Promote human-approved examples to golden dataset
python -m scripts.dataset.ingest_qa --agent planner
```
