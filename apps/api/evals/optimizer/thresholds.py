"""Quality gate thresholds for optimizer eval.

Values are populated from the live baseline run. Until then,
THRESHOLD_COHERENCE_MIN is None and any code that tries to use it
for gating must check `if THRESHOLD_COHERENCE_MIN is None: skip
gate`.

See ADR-0016 for the rationale on threshold selection.
"""

from __future__ import annotations

from typing import Final

THRESHOLD_LABEL_CORRECT: Final[float] = 1.0  # deterministic, always 100%
THRESHOLD_COHERENCE_MIN: Final[float | None] = None  # set after baseline
THRESHOLD_HIGH_VARIANCE_MAX_PCT: Final[float] = 0.20  # 20% of scenarios
