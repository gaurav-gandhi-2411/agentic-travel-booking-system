# Optimizer Eval Baselines

This directory contains pinned baseline runs. The current canonical baseline is dated
2026-05-17, post-Phase 2C.2 substrate fixes.

Baseline files live in `evals/optimizer/runs/` (JSONL) and `evals/optimizer/reports/`
(scored markdown reports). They are committed to the repository so future regressions can
be measured against a known-good state.

## Canonical baseline (2026-05-17)

| Profile | Run file | Report |
|---|---|---|
| demo-haiku | `runs/20260517T163929_demo-haiku.jsonl` | `reports/20260517T184135_report.md` |
| demo-llama | `runs/20260517T171143_demo-llama.jsonl` | `reports/20260517T185649_report.md` |

### Results summary

- **Haiku**: 24/24 completion, 100% label-correct, coherence avg 5.0, variance **0.0**,
  high-variance archetypes 0. Establishes the upper bound for explanation coherence on this
  dataset. Variance dropped from 0.452 (Phase 2C.1) to 0.0 — the S1 departure-time
  hallucination fix is validated.

- **Llama**: 21/24 completion (Groq TPD constraint per Issue #16), 100% label-correct on
  completed scenarios, coherence avg 4.881, variance 0.107. Passes
  `THRESHOLD_COMPLETION_MIN = 0.83`. The 3 failures (opt-022/023/024) are a quota boundary,
  not a model or code regression.

### Thresholds (from `evals/optimizer/thresholds.py`)

```
THRESHOLD_COMPLETION_MIN          = 0.83
THRESHOLD_LABEL_CORRECT_COMPLETED = 1.0
THRESHOLD_COHERENCE_MIN           = 4.0
THRESHOLD_HIGH_VARIANCE_MAX_PCT   = 0.20
```

Both profiles clear all thresholds. The nightly eval workflow (`eval-nightly` job in
`.github/workflows/eval-optimizer.yml`) checks these gates on every run and opens a
`nightly-eval-failure` issue on violation.

## Adding a new baseline

Run the eval and scorer against the target profiles, then commit the JSONL and report files
to this repo. Update the table above and the threshold file if the new baseline resets
the reference floor.
