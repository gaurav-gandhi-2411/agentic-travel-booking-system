# Project Spec: Phase 2D Iteration 6 — Eval Rigor

## Goal

The final iteration in the Phase 2D sequence. Two related eval-methodology fixes that make cross-profile coherence numbers credible and eval runs repeatable. These were deferred from iteration 2 when the production audit expanded into iterations 2-5.

**Issue #21 — Cross-profile judge consistency.** Cross-profile coherence comparisons are not strictly valid because the judge model varies: Qwen3-32B (primary, on Groq) is used until its TPD rate limit is hit, then it silently falls back to Sonnet (Anthropic). A coherence score judged by Qwen isn't directly comparable to one judged by Sonnet, so any cross-profile comparison that mixes judges is methodologically unsound. Fix via **Approach 3**: record which judge scored every result, and gate cross-profile comparisons on a same-judge requirement — refuse (or loudly warn) when comparing runs that used different judges, rather than silently producing incomparable numbers.

**Issue #20 — Judge cache poisoning.** The judge cache gets poisoned by entries where the judge call failed to parse but a default score of 1 was cached. On subsequent runs these poisoned entries are served as if they were real scores, silently corrupting results. Fix: validate cache entries on read — treat failed-parse/score=1-sentinel entries as cache misses and re-run the judge. Plus a one-time cleanup utility to purge existing poisoned entries from the current cache.

Together: ~3-4 hours. These ship as one or two PRs (they're both in the eval subsystem and related).

## Current state

See `CURRENT_STATE.md` (current through iteration 5). Critical facts:

- Production is fully current and self-monitoring (iterations 3-5): backend v0.6.0, frontend current, staleness guardrail live on both surfaces. This iteration does NOT touch production — it's eval-subsystem-only.
- The eval framework lives under `apps/api/src/travel_agent/evals/` (and `apps/api/evals/` for the optimizer eval harness — confirm exact layout via the repo). The orchestrator discovers exact file paths via grep.
- Judge model selection: Qwen3-32B (profile `eval-judge-qwen3-32b`) is primary on Groq; Sonnet (`eval-judge-sonnet`) is the fallback when Groq TPD is exhausted. The selection/fallback logic is in the eval runner/scorer — orchestrator locates it.
- The judge cache is a persistent cache keyed on (scenario + completion) that stores judge scores to avoid re-running expensive judge calls. Location discovered via grep ("judge cache", "score", caching in evals/).
- There's a nightly cron running the golden set. Issue #15 documents that Llama evals are bounded by Groq TPD — relevant context for why the fallback exists.
- ADRs through 0025 exist. Prior eval ADRs: 0016 (LLM judge design), and the judge profile work in 0019. Create ADR-0026 for this iteration.
- `[skip ci]` guardrail is an active required check. Don't trip it.
- The judge thresholds live in `apps/api/evals/optimizer/thresholds.py` (HARD RULE — don't change thresholds; this iteration adds rigor, not threshold changes).

## Scope

### In scope (this iteration)

**Part A — Issue #21 cross-profile judge consistency (Approach 3):**

1. Locate the judge selection + scoring logic (runner.py / scorer.py under evals/). Understand:
   - How the judge model is chosen per run and per scenario
   - When/how the Qwen→Sonnet fallback fires
   - Whether a single run can mix judges across scenarios (e.g., Qwen for the first N scenarios, then Sonnet after TPD exhaustion mid-run)

2. Implement Approach 3:
   - **Record the judge model in every scored record.** Each result that carries a coherence score must also carry the `judge_model` that produced it (e.g., `eval-judge-qwen3-32b` or `eval-judge-sonnet`). Persist this in the result JSON / scored output.
   - **Surface judge_model in scorer output** — when a run is summarized, show which judge(s) scored it. If a single run mixed judges (due to mid-run TPD fallback), flag that explicitly in the output ("⚠️ mixed judges in this run: N scenarios by Qwen, M by Sonnet — coherence not internally comparable").
   - **Gate cross-profile comparisons.** Wherever the eval tooling compares coherence across profiles (the cross-profile comparison/report path), require the compared runs to share the same judge_model. If they differ, refuse the comparison with a clear error/warning explaining the runs used different judges and are not directly comparable. Don't silently produce a misleading delta.

3. Do NOT force a single judge or change the fallback behavior itself (that's Approach 1/2, rejected). The fallback stays; we just make its effect VISIBLE and prevent silent incomparable comparisons.

4. Re-baselining is OUT OF SCOPE (cost discipline): Approach 3 ships the *mechanism* to detect incomparability, not comparable numbers. Re-running profiles with a consistent judge (e.g., all-Sonnet) is a separate on-demand action that costs paid Anthropic spend (~$0.50-1.50) and is triggered only when a stakeholder actually needs cross-profile numbers. Document this in the ADR; do not run paid re-baselines in this iteration.

**Part B — Issue #20 judge cache poisoning:**

5. Locate the judge cache implementation and identify the poisoned-entry shape: entries where the judge call failed to parse but a sentinel/default score (score=1) was cached as if valid.

6. **Validate on read:** when reading a cached judge score, detect the poisoned shape (failed-parse marker, or the score=1 sentinel that indicates a parse failure rather than a genuine score of 1). Treat poisoned entries as cache MISSES — re-run the judge call to get a real score. Add a warning log when a poisoned entry is detected and bypassed.
   - Important nuance: a genuine judge score of 1 (a real low score) must be distinguishable from a parse-failure-default of 1. If the current cache can't distinguish them, the fix must add a marker (e.g., store `parse_failed: true` alongside, or store the raw judge response so validity can be re-derived). Determine the cleanest discriminator during build. If genuine-1 and failed-1 are truly indistinguishable in existing cached data, the cleanup utility (below) handles the existing ambiguous entries and the read-validation prevents new ones.

7. **Cleanup utility:** a one-time script / eval CLI flag (e.g., `--purge-poisoned-judge-cache`) that scans the existing judge cache, removes entries matching the poisoned shape, reports counts (how many purged, how many retained), and is safe to run idempotently. GG (or the orchestrator) runs it once to clean the current cache.

8. **Tests:**
   - Cache read-validation rejects a poisoned entry and triggers a re-run
   - A genuine score (including a genuine low score) is NOT falsely purged/rejected
   - Cleanup utility removes only poisoned entries (no false positives on genuine scores)

### Out of scope (do not build)

- Approach 1 or 2 for #21 (pinning a single judge / failing or waiting on TPD) — Approach 3 chosen
- Paid re-baseline runs with a consistent judge (on-demand only, not this iteration)
- Changing judge models, adding judge models, or changing the Qwen→Sonnet fallback behavior itself
- Changing eval thresholds (thresholds.py is a HARD RULE)
- Changing the nightly cron behavior (Issue #15 context — don't destabilize it)
- Any production deploy or production touch (this is eval-subsystem-only)
- Any application/runtime logic outside the eval subsystem
- Phase 3 features
- New eval scenarios

### Remaining backlog after this iteration (for awareness, NOT in scope)

After iteration 6, the open Phase 2D follow-ups are housekeeping/low-priority: #47 (closed), #54 (docs-scope staleness refinement, low priority), #49 (BACK-001 migrated item), any setup-node-style deprecations. The substantive Phase 2D work (production currency, observability, CI hygiene, eval rigor) is complete after this iteration.

## Tech stack

- Python 3.12, Pydantic (eval result models)
- pytest (tests)
- The existing eval framework libraries (no new deps)
- Groq (Qwen judge) + Anthropic (Sonnet judge) — but NO paid re-baseline runs this iteration

No new dependencies expected.

## Architecture

```
apps/api/src/travel_agent/evals/   (and/or apps/api/evals/ — confirm layout)
├── optimizer/
│   ├── runner.py          # MODIFIED: record judge_model per scored record
│   ├── scorer.py          # MODIFIED: surface judge_model + flag mixed-judge runs + gate cross-profile comparison
│   └── judge_cache.py     # MODIFIED (or wherever cache lives): validate-on-read, reject poisoned entries
├── (result models)        # MODIFIED: add judge_model field to scored-result schema
└── purge_poisoned_cache.py  # NEW: one-time cleanup utility (or a CLI flag on the runner)

apps/api/tests/unit/evals/
├── test_judge_cache.py    # NEW/MODIFIED: read-validation + cleanup tests
└── test_scorer.py         # MODIFIED: judge_model recording + cross-profile gate tests

docs/architecture/adr/
└── 0026-eval-rigor.md     # NEW: Approach 3 rationale, cache-poisoning fix, re-baseline-is-on-demand note

CURRENT_STATE.md           # MODIFIED: mark #20, #21 closed; eval rigor complete
```

Exact paths confirmed by the orchestrator via grep during Phase 0.

## Verification commands

```yaml
- name: backend-tests
  cmd: cd apps/api && pytest -v
  required: true
- name: eval-tests
  cmd: cd apps/api && pytest tests/unit/evals/ -v
  required: true
- name: backend-lint
  cmd: cd apps/api && ruff check .
  required: true
- name: backend-types
  cmd: cd apps/api && mypy src
  required: true
- name: coverage-gate
  cmd: cd apps/api && pytest --cov=src --cov-fail-under=80
  required: true
```

## Subagent usage rules

- executor: locate eval logic, implement recording/gating/validation, write cleanup utility, tests, ADR/docs
- verifier: run the test/lint/type/coverage commands
- The cleanup utility (#20) can be RUN against the local/dev judge cache for verification, but does not touch any production system (evals are dev/CI-side). No production action in this iteration.

## Escalation rules (orchestrator MUST ask before doing)

- Ask if genuine-score-1 and failed-parse-1 are truly indistinguishable in existing cached data AND the discriminator requires a schema change to the cache format that would invalidate the whole existing cache (vs. a clean migration) — surface the trade-off.
- Ask before any change that would alter existing baseline NUMBERS (this iteration adds metadata and validation; it should not change what a correct score IS). If recording judge_model or validating the cache would change a published baseline figure, surface it.
- Ask if the cross-profile comparison gate would break the nightly cron or any existing automated report (the cron may do cross-profile reporting — if so, the gate must degrade gracefully, not crash the cron).
- Ask before running any PAID judge call (Sonnet). This iteration should be implementable and testable WITHOUT paid re-baselines — tests use mocked/fixture judge responses. If something genuinely requires a live Sonnet call, surface the cost first.
- Ask if a single executor pass would touch more than 6 files.
- Never set ANTHROPIC_API_KEY. No [skip ci]. Ask before new dependencies.

## Hard rules (DO NOT touch)

- `apps/api/evals/optimizer/thresholds.py` values — no threshold changes
- The Qwen→Sonnet fallback behavior itself — don't remove or alter it; only make it visible
- Judge model identities (`eval-judge-qwen3-32b`, `eval-judge-sonnet`) — don't rename
- The nightly cron's core behavior — don't destabilize (Issue #15 context)
- Production systems — this iteration is eval-subsystem-only, zero production touch
- All HARD RULE files from prior specs (refine.py, search.py, llm_routing.yaml profiles, prompts, streaming events)
- All existing ADRs (0001-0025) — create 0026
- Existing structured log event names — add new ones if needed, don't rename
- No paid re-baseline runs (on-demand only, documented in ADR)

## Budget

- Soft target: 1 Max plan window
- Hard cap: escalate if executor invocations exceed 20
- Cost check: /cost at midpoint (after Part A) and close-out. NOTE: this iteration must incur ZERO paid Anthropic API spend — tests use mocked judge responses, no live Sonnet calls.

## Success criteria (orchestrator verifies ALL before declaring done)

**Part A — judge consistency (#21):**
- [ ] judge_model recorded in every scored result
- [ ] scorer output surfaces judge_model; flags mixed-judge runs explicitly
- [ ] cross-profile comparison gated on same-judge — refuses/warns clearly when judges differ, no silent incomparable delta
- [ ] fallback behavior unchanged (only made visible)
- [ ] no baseline NUMBERS changed (metadata added, scores unchanged)
- [ ] ADR-0026 documents Approach 3 + the on-demand re-baseline note

**Part B — cache poisoning (#20):**
- [ ] cache read-validation detects + rejects poisoned (failed-parse/sentinel-1) entries, treats as miss, re-runs
- [ ] genuine scores (including genuine low scores) preserved — not falsely rejected
- [ ] cleanup utility purges only poisoned entries, reports counts, idempotent
- [ ] tests cover: poisoned-rejected, genuine-preserved, cleanup-no-false-positives
- [ ] cleanup run once against the dev judge cache, counts reported

**Both:**
- [ ] eval tests pass; backend tests/lint/types/coverage(≥80%) green
- [ ] ZERO paid Anthropic spend (mocked judge responses in tests)
- [ ] no production touch, no [skip ci], no ANTHROPIC_API_KEY
- [ ] no HARD RULE file modified
- [ ] PR(s) opened, CI green, merged; staging deploy (if triggered by merge) green
- [ ] CURRENT_STATE.md updated; #20, #21 closed

## Build order

1. Phase 0 (read-only): locate the eval judge logic + cache implementation, confirm file layout, identify the poisoned-entry shape and whether genuine-1/failed-1 are distinguishable. Report briefly, then proceed (no gate needed unless the cache-schema trade-off from escalation rules surfaces).
2. Part A (#21 judge consistency) — record + surface + gate. Tests.
3. Part B (#20 cache poisoning) — validate-on-read + cleanup utility. Tests. Run cleanup once on dev cache.
4. ADR-0026 + CURRENT_STATE.md + close #20/#21.
5. PR(s), CI green, merge. /cost.

## Notes for the orchestrator

- Max plan covers Opus 4.7 + Sonnet for the ORCHESTRATION — but this iteration must NOT make paid judge API calls. Tests mock judge responses. No live Sonnet re-baselines.
- This is the last iteration in the Phase 2D sequence (4→5→6). After it, the substantive Phase 2D backlog is complete.
- Approach 3 for #21 was chosen deliberately over pinning a single judge: it's the least fragile (doesn't break the cron or fail on TPD), and it makes the comparability constraint EXPLICIT rather than silently producing incomparable numbers. The fallback stays; we surface its effect.
- The key subtlety in #20: distinguishing a genuine judge score of 1 from a parse-failure default of 1. Get the discriminator right — the read-validation and cleanup must not purge genuine low scores. If existing cached data can't distinguish them, the cleanup handles the ambiguous existing entries and read-validation (with a proper marker) prevents new ones.
- Read #20 and #21 via `gh issue view` for original context before starting.
- Zero production touch this iteration — it's entirely in the eval subsystem.
