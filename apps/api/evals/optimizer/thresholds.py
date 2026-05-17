"""Quality gate thresholds for optimizer eval.

Values set from the 24-scenario baseline run on 2026-05-17.
See ADR-0016 for threshold rationale.
"""

from __future__ import annotations

from typing import Final

# Completion rate — fraction of scenarios that produced archetypes.
# Baseline: haiku 24/24=100%, llama 21/24=87.5%.
# Floor set at 0.83 = baseline llama minus 0.045. This is TIGHT
# ON PURPOSE: a single additional runner-phase failure flips llama
# to FAIL, which is diagnostic, not noise. See Issue #15 for the
# runner-quota root cause we're choosing to surface via the gate
# rather than hide via a loose threshold.
THRESHOLD_COMPLETION_MIN: Final[float] = 0.83

# Label correctness on COMPLETED scenarios only (denominator excludes
# runner failures). Pareto math is deterministic — once archetypes are
# produced the label must be correct. 100% is the strict target.
THRESHOLD_LABEL_CORRECT_COMPLETED: Final[float] = 1.0

# Coherence average on scored archetypes. Baseline lower bound was
# llama at 4.571. Threshold = baseline - 0.5 (one-sigma equivalent
# given variance < 0.5 on both profiles).
THRESHOLD_COHERENCE_MIN: Final[float] = 4.0

# High-variance fraction (archetypes where max-min score > 2 across
# 3 judge samples). Baseline: haiku 2/24=8%, llama 0/24=0%.
# 20% gives headroom while flagging rubric-ambiguity creep.
THRESHOLD_HIGH_VARIANCE_MAX_PCT: Final[float] = 0.20

# Reference: baseline run committed in PR #13
# Reports: evals/optimizer/reports/20260517T130642_report.md (haiku)
#          evals/optimizer/reports/20260517T132554_report.md (llama)
