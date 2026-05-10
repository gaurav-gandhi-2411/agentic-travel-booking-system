# Eval Results

Run outputs are gitignored (large JSON). Only summary CSVs are committed.

## Gitignore Policy

```
evals/results/*.json       # full run outputs — not committed
evals/results/summaries/   # committed — one CSV per run
```

## Summary Format

`summaries/<agent>_<mode>_<date>.csv`:

```csv
agent,mode,metric,value,run_date,git_sha
planner,quick,exact_match,0.92,2026-01-01,abc1234
planner,quick,preference_win_rate,0.87,2026-01-01,abc1234
```

## Regression Baseline

`baseline.json` in this directory stores the last accepted full-eval scores.
CI compares new runs against baseline; >2% regression on any metric blocks merge.
