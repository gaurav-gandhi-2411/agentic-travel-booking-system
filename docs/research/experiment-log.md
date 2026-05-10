# Experiment Log

Chronological record of fine-tuning runs and ablations.

## Format

Each entry:

```
### YYYY-MM-DD — <short description>

**Run ID**: exp-001
**Agent**: planner
**Base model**: Qwen2.5-7B
**LoRA rank**: 16 / alpha: 16
**Dataset**: 1000 train / 100 eval
**Training steps**: 500
**Hardware**: <GPU type>
**Runtime**: <hh:mm>

**Results**:
| Metric | Before | After | Δ |
|--------|--------|-------|---|
| exact_match | 0.71 | 0.92 | +0.21 |
| preference_win_rate | 0.64 | 0.87 | +0.23 |

**Notes**: <observations, surprises, next steps>
```

## Entries

*(No entries yet — first run in Phase 6.5)*
