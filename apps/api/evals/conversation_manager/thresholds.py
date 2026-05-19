"""Quality gate thresholds for conversation_manager eval.

Action accuracy of 0.90 allows at most 1 misclassification in 15 scenarios.
Latency gates match ADR-0019 §Eval Gate: warn at 2500ms, hard fail at 4000ms.
"""

from __future__ import annotations

from typing import Final

# Fraction of scenarios where the agent chose the correct action (REFINE/REPLAN/NO_OP).
# 15 scenarios; 0.90 = floor of 13-15 correct. One scenario of slack.
CONVERSATION_ACTION_ACCURACY_MIN: Final[float] = 0.90

# NO_OP explanation quality (1-5 scale, LLM judge, optional).
# 4.0 mirrors the optimizer archetype coherence floor.
CONVERSATION_NO_OP_COHERENCE_MIN: Final[float] = 4.0

# p95 latency gates from ADR-0019 §Eval Gate. Measured across all scored scenarios.
CONVERSATION_LATENCY_P95_WARN_MS: Final[int] = 2500  # log WARNING; eval continues
CONVERSATION_LATENCY_P95_MAX_MS: Final[int] = 4000   # FAIL
