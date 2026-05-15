# ADR-0015: Optimizer Eval Harness Design

**Status:** Accepted — 2026-05-16

---

## Context

The OptimizerAgent runs a Pareto frontier algorithm (deterministic) then calls an LLM
to generate natural-language explanations (non-deterministic). We need to measure:

1. Whether the Pareto math selects the correct archetypes (deterministic — 100% target)
2. Whether explanations are coherent and accurate (LLM-judged, deferred to Phase 2c)
3. Latency and cost across three LLM profiles (demo-haiku, demo-llama, demo-qwen)

The eval runs in CI (non-gating in Phase 2b; gating in Phase 2c).

---

## Decision

### Inputs

20 synthetic flight result sets generated programmatically from SyntheticProvider.
Flight sets cover five routes × four time windows:
- Routes: DEL→DXB, BOM→BKK, DEL→SIN, BOM→CMB, DEL→KUL
- Windows: 4 consecutive 7-day windows per route (June 2026)

### Outputs

For each (flight_set, profile) pair: two Archetype objects with labels + explanations.

### Scoring axes

1. **Label correctness** (deterministic): the selected archetype.label must match what
   a pure Pareto run would pick. Target: 100% across all profiles.
2. **Explanation coherence** (LLM-judged by claude-sonnet-4-6 eval profile):
   binary yes/no per explanation. **Deferred to Phase 2c** (no gate in Phase 2b).
3. **Cost** (reported, no gate): USD per (search, profile) via pricing module.
4. **Latency** (reported): p50, p95 per profile.

### Judge model (Phase 2c, deferred)

claude-sonnet-4-6 via `eval` profile. Inherent self-judgment bias for the Haiku profile
is acknowledged and accepted for v1. Pairwise preference deferred to Phase 2c.

### Pass criteria (Phase 2b)

- Label correctness: 100% (hard gate in Phase 2c; measurement-only in Phase 2b)
- Coherence: no gate in Phase 2b; baseline measurement only

### Infrastructure

- Runner: `evals/optimizer/runner.py`
- Scorer: `evals/optimizer/scorer.py`
- Run output: `evals/optimizer/runs/<timestamp>_<profile>.jsonl`
- Reports: `evals/optimizer/reports/<timestamp>_report.md`
- CI: `.github/workflows/eval-optimizer.yml` (non-blocking; triggered by label or weekly)

---

## Consequences

**Positive:**
- Deterministic label-correctness check runs in CI with zero API keys (dry-run mode)
- Dry-run baseline committed to repo: documents current Pareto behaviour
- Scorer is standalone and can be re-run against any run JSONL without rerunning the LLM

**Negative:**
- Explanation coherence is not measured in Phase 2b — relying on human spot-checks only
- Self-judgment bias for LLM-as-judge is deferred rather than resolved

**Neutral:**
- 20 scenarios (5 routes × 4 windows) is sufficient for a Phase 2b baseline
- ADR-0010 governs the broader eval harness; this ADR is scoped to the Optimizer only

---

*References: ADR-0010 (eval harness design), ADR-0006 (Pareto frontier archetypes)*
