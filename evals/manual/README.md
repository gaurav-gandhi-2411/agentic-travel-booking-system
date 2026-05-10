# Manual QA

Human review queue for dataset examples before they graduate to golden datasets.

## Workflow

1. **Input queue** (`input/`): Raw synthetic examples from the generation pipeline awaiting review.
2. **Review**: Reviewer opens each file, edits expected outputs as needed, marks `"reviewed_by": "human"`.
3. **Approved** (`approved/`): Reviewed examples ready to promote to `evals/datasets/<agent>.jsonl`.
4. **Rejected** (`rejected/`): Examples with unfixable issues; kept for analysis but excluded from training.

## Review Targets (Phase 6.5)

- 100 examples per agent × 6 agents = 600 total reviews
- Estimated time: 30–60 min per agent (5–10 s per example)
- Reviewers: project maintainers or designated contributors

## Promotion Command

```bash
# Phase 3.5: implemented in scripts/dataset/ingest_qa.py
python -m scripts.dataset.ingest_qa --agent planner
```

This moves approved examples to `evals/datasets/planner.jsonl` and archives inputs.
