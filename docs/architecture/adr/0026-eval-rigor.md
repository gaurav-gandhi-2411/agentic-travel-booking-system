# ADR-0026 — Eval Rigor: Judge Consistency Gate and Cache Poison Fix

**Date:** 2026-05-31  
**Status:** Accepted  
**Phase:** 2D Iteration 6

---

## Context

Two eval-methodology defects were deferred from earlier iterations.

**Issue #20 — Judge cache poisoning.** When the judge LLM (Qwen3-32B on Groq) hits its TPD
rate limit, all three retry attempts fail and the scorer writes a sentinel cache entry with
`coherence_score=1`, `coherence_reason="Judge output could not be parsed after 3 attempts"`,
and `all_scores=[]`. On subsequent runs — including runs using a *different* judge — the cache
serves this sentinel as a real score, silently inflating failure rates. A manual purge was
required after the first occurrence (Phase 2C.2 GPT-OSS-120B baseline: 45/72 cache entries
were stale `score=1` sentinels; re-score with Sonnet returned 4.75).

**Issue #21 — Cross-profile coherence comparability.** Coherence scores from different judge
models are not directly comparable. Phase 2C baselines used Qwen3-32B for Haiku and Llama but
Sonnet for GPT-OSS-120B (Qwen3-32B TPD was exhausted). A table comparing all three profiles
implies a controlled comparison, which it is not. Additionally, a single scorer run can receive
cache hits from a prior run that used a different judge — mixing judges within a single output
report.

---

## Decision

### Issue #20 — validate-on-read + explicit marker

**Add `parse_failed: bool = False` and `judge_model: str = ""` to `JudgeScore`** (the Pydantic
model that is both the in-memory result and the cache serialisation format).

**Validate-on-read:** In `CoherenceJudge.score()`, when a cache hit is found, check
`cached.parse_failed or not cached.all_scores`. If either is True, treat the entry as a cache
miss and re-run the judge. Log a `judge_cache_poisoned_entry_bypassed` warning.

**Why `all_scores == []` as the secondary discriminator:** the parse-failure path has always
written `all_scores=[]` (no samples could be parsed), while a genuine score — even a genuine 1
— produces `all_scores=[1, ...]` from actual parsed samples. This is a clean discriminator
that handles existing cached entries written before `parse_failed` existed.

**One-time cleanup utility** (`purge_poisoned_cache.py`): scans `judge_cache.json`, removes
entries matching `parse_failed=True OR all_scores==[]`, reports counts. Safe to run
idempotently. Current cache (306 entries) had 0 poisoned entries at time of implementation.

### Issue #21 — Approach 3: record + gate, no forced re-baseline

Three approaches were considered:

| Approach | Description | Verdict |
|---|---|---|
| 1 — Pin single judge | Force all runs to use Qwen3-32B; fail on TPD instead of falling back | Rejected: breaks nightly cron when Groq TPD is exhausted |
| 2 — Fail/wait on TPD | Surface TPD as a hard failure; require operator intervention | Rejected: same issue — destabilises nightly CI |
| **3 — Record + gate** | **Record which judge scored each entry; gate cross-profile comparisons on same-judge** | **Accepted** |

**Record judge_model per cache entry:** `CoherenceJudge.__init__` stores `self._judge_profile`
(the profile name string, e.g., `"eval-judge-qwen3-32b"`). Every newly written cache entry
carries `judge_model=self._judge_profile`. The 306 existing entries retain `judge_model=""`
(the field default) and are treated as "unknown/unattributed."

**Surface in scorer output:** `print_summary` now shows the judge(s) used per profile run. If
a run received cache hits from a prior run using a different judge (mixed judges), it logs a
`⚠  Mixed judges` warning on the profile summary line.

**Gate cross-profile comparisons:** `check_cross_profile_judge_consistency(summaries)` is
called in `write_report` and `main`. Gate rules:
- No coherence data → passes vacuously.
- Any within-run judge mix → refuses.
- All entries unknown/legacy → refuses.
- Two or more distinct known judges across profiles → refuses.
- One known judge with some legacy unknowns → passes with a warning.
- All entries share one known judge, no unknowns → passes cleanly.

The gate is **non-fatal for the nightly cron**: it prints a warning or error to stdout but
does not change the process exit code. `_check_gates` (per-profile threshold checking) remains
the sole determinant of exit code. This ensures the cron cannot crash or silently skip due to
judge attribution gaps in legacy cache entries.

### Backward compatibility of existing 306 cache entries

The two new fields have Pydantic defaults (`judge_model=""`, `parse_failed=False`). Loading
existing entries via `JudgeScore.model_validate(cache[key])` succeeds without modification.

Existing entries with `judge_model=""` are treated as unknown by the cross-profile gate —
they do NOT pass the gate as "same judge." They age out as the cache refreshes: each time a
scenario is re-scored, the new entry carries the real judge profile. Over several nightly eval
cycles, the unknown-attributed entries will be replaced.

---

## Consequences

**Positive:**
- Cache poisoning is prevented going forward; existing poisoned entries are auto-healed on
  re-score (validate-on-read treats them as cache misses).
- Cross-profile coherence comparisons that mix judges are now refused rather than silently
  producing misleading deltas.
- Every scored result carries provenance (which judge model produced it).

**Negative / accepted:**
- The 306 existing cache entries carry `judge_model=""`. Until they refresh, the cross-profile
  gate will refuse or warn on any multi-profile comparison that touches old entries. This is
  correct behaviour — those entries genuinely lack judge attribution.
- No baseline NUMBERS were changed by this iteration. Scores produced before this change
  remain in the cache and in existing reports unchanged.

**Out of scope (on-demand only):**
- **Re-baselining all profiles with a single consistent judge** is deferred as an explicit
  on-demand action. It requires paid Anthropic spend (~$0.50–$1.50 for all-Sonnet re-score
  of the 24-scenario × N-profile set) and should be triggered only when a stakeholder needs
  a fully comparable cross-profile table. To run: `python -m evals.optimizer.scorer --all
  --judge-profile eval-judge-sonnet` after ensuring the cache is purged of old entries.

---

## Alternatives considered

**TTL on failed entries (Issue #20):** auto-expire failed-parse entries after 1 hour. Rejected
because TTL requires either a timestamp field or an external TTL store, adds complexity, and
doesn't address the root cause (lack of a failure marker). The `parse_failed` field is
simpler and self-describing.

**Approach 1/2 for Issue #21:** see table above. Both break nightly CI on TPD exhaustion,
which is a known recurring event (Issue #15). Approach 3 treats the fallback as an observable
event rather than a failure mode.
