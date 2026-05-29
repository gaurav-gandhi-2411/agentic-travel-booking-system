# Project Spec: Phase 2D Iteration 2 — Production Audit + Eval Rigor

## Goal

Two related-but-distinct workstreams in one iteration, bundled with explicit escalation rules so the iteration can split cleanly if production audit surfaces unexpected scope.

**Part A — Production secret audit (Issue #37).** Phase 2D iteration 1 Part A discovered that staging's `UPSTASH_REDIS_URL` had been a placeholder value silently breaking caching for weeks. Production is currently live with real traffic. Audit all production secrets in GCP Secret Manager for similar placeholder values or misconfigurations. Rotate as needed. Verify production cache backend selection via the observability we just shipped.

**Part B — Eval rigor (Issues #21 + #20).** Cross-profile coherence numbers in current evals are not strictly comparable because the judge model varies (Qwen3-32B primary, Sonnet fallback when TPD exhausted). And the judge cache gets poisoned by failed-parse score=1 entries that compound over interrupted eval runs. Both issues affect the credibility of our cross-profile baseline numbers. Fix both so evals produce comparable, repeatable numbers.

Together: ~4-5 hours of orchestrator work. Two PRs (one per Part). Part A is risk reduction; Part B is story strengthening.

## Current state

See `CURRENT_STATE.md` for non-obvious context. Critical items relevant to this iteration:

- Phase 2D iteration 1 closed. Top of main commits: 375e764 (Part C — pip-audit fix), b872164 (Part B — `[skip ci]` guardrail), 16d3b53 (Part A — cache observability).
- Issues closed in iteration 1: #18, #30, #31. Filed new: #37 (production audit), #41 (setup-node v5 upgrade), #42 (backlog.md migration).
- Production secret audit (this iteration's Part A) was triggered by the staging placeholder URL finding in iteration 1. Production currently runs with real traffic per GG's confirmation — caution warranted.
- Cache observability (ADR-0021 from iteration 1 Part A) is the diagnostic surface we'll use to verify production cache backend selection.
- Eight secrets used by the system: `upstash-redis-url`, `upstash-redis-token`, `anthropic-api-key`, `groq-api-key`, `nvidia-nim-api-key`, `aviasales-api-token`, `langfuse-public-key`, `langfuse-secret-key`, `demo-api-key`. Plus any iteration may surface additional secrets we don't know about.
- Eval framework lives at `apps/api/src/travel_agent/evals/`. The judge model selection logic is in `evals/optimizer/runner.py` or `evals/optimizer/scorer.py` — orchestrator discovers via grep.

## Scope

### In scope (this iteration)

**Part A — Production secret audit (Issue #37):**

1. Inventory all production-bound GCP Secret Manager secrets in the `agentic-travel-booking-system` project. Use:

```bash
gcloud secrets list --project agentic-travel-booking-system --format="table(name, createTime, replication.policy)"
```

2. For each secret, check the latest version value. Subagents read via:

```bash
gcloud secrets versions access latest --secret=<secret-name> --project=agentic-travel-booking-system
```

Don't log the full value. Capture only:
- Prefix pattern (first 10 chars + length) for diagnostic
- Whether it appears to be a placeholder (e.g., contains `PLACEHOLDER`, `TODO`, `your-`, `xxx`, or is suspiciously short)
- Whether the format matches expected pattern for the secret type (e.g., `rediss://` for Redis URL, `sk-ant-` prefix for Anthropic key, `gsk_` for Groq, etc.)

3. For each production Cloud Run service (`agentic-travel-booking-api-prod` confirmed; check for others), confirm which secrets are bound and at what version:

```bash
gcloud run services describe agentic-travel-booking-api-prod \
  --region asia-south1 \
  --format="value(spec.template.spec.containers[0].env)"
```

4. Verify production cache backend via the observability shipped in iteration 1:

```bash
gcloud logging read 'resource.labels.service_name="agentic-travel-booking-api-prod" AND jsonPayload.event="cache_backend_selected"' \
  --limit 3 --freshness=7d --project agentic-travel-booking-system
```

Expected outcome: `backend=redis` on recent logs. If `backend=in_memory`, production has the same silent-fallback issue staging had.

5. If any production secret is a placeholder or any production service is on in-memory fallback:
   - **STOP** and surface to GG via escalation
   - GG rotates the secret(s) using the same pattern as iteration 1 Part A
   - Orchestrator verifies the fix via the observability logs
   - Document the finding in `CURRENT_STATE.md` and ADR-0021 (amend, don't replace)

6. If all production secrets check out:
   - Update CURRENT_STATE.md to note the audit was completed and what was found (or what was clean)
   - Close Issue #37 with the audit summary

**Part B — Eval rigor (Issues #21 + #20):**

*Issue #21 — Cross-profile judge consistency:*

1. Read the current judge selection logic in `apps/api/src/travel_agent/evals/optimizer/runner.py` and `scorer.py`. Understand:
   - When primary judge (Qwen3-32B on Groq) is used
   - When fallback judge (Sonnet on Anthropic) kicks in
   - Whether the same eval batch can mix judges across scenarios

2. Implement one of three fixes (orchestrator picks based on what the code allows most cleanly):
   - **Approach 1:** Pin a single judge model per eval run. If primary fails TPD, fail the run cleanly rather than silently falling back. Document the TPD constraint as a known operational limit.
   - **Approach 2:** Always use the same judge across all scenarios in a single run, even if it means waiting for TPD reset. Add a `--judge` CLI flag for explicit override.
   - **Approach 3:** Mark judge model in every scored record and surface it in the scorer output. Cross-profile comparisons gated on "same judge across both runs being compared." Doesn't fix the eval but makes comparability explicit.

   Lean Approach 1 if the eval framework supports it cleanly; Approach 3 if Approach 1 requires too much refactor.

3. Update eval output to surface judge model used per scenario.

4. Re-run nightly cron's golden set with the fix to confirm baseline numbers are still in expected range.

*Issue #20 — Judge cache poisoning:*

1. Identify the judge cache location and structure. Likely in `apps/api/src/travel_agent/evals/` somewhere — orchestrator discovers via grep.

2. Implement validation on cache read: if cached entry has `score=1` AND `parse_status=failed_parse` (or similar marker), treat as cache miss and re-run the judge call. Optionally surface a warning log.

3. Provide a one-time cache cleanup utility:
   - Script or eval CLI flag `--purge-failed-parse-cache` that scans the judge cache, removes entries with the poisoned shape, and reports counts.
   - GG runs this manually post-deploy to clean the existing poisoned entries.

4. Unit tests for both:
   - Cache validation rejects poisoned entries
   - Cleanup utility removes correct entries without false positives

### Out of scope (do not build)

- Phase 2D issues NOT listed above: #14, #15, #29, #33, #34, #35, #41, #42 — separate iterations
- Phase 3 work (real hotel data, BookingAgent, multi-tenancy)
- Changing the judge model itself (Qwen3-32B stays primary, Sonnet stays fallback unless Issue #21's chosen approach removes the fallback)
- Adding new judge models
- Modifying production secrets that are working correctly (audit-only unless placeholder found)
- New eval scenarios or threshold changes
- Modifying `apps/api/src/travel_agent/api/routes/refine.py` or any other HARD RULE file from CURRENT_STATE.md
- Phase 3 production hardening (real load testing, multi-region, etc.)

## Tech stack

Only what's relevant to this iteration:

- Python 3.12 (backend)
- pytest (testing)
- google-cloud-sdk (already installed for prior iterations) — `gcloud` commands
- structlog (existing logging convention)
- The eval framework's existing libraries (no new deps expected)

No new dependencies. If anything needs adding, escalate.

## Architecture

NEW or MODIFIED files this iteration:

```
apps/api/src/travel_agent/evals/optimizer/
├── runner.py                              # MODIFIED: judge selection logic per Issue #21 chosen approach
├── scorer.py                              # MODIFIED: surface judge model per scenario in output
└── judge_cache.py (if exists, else inline)  # MODIFIED: cache read validation per Issue #20

apps/api/scripts/ or apps/api/src/travel_agent/evals/
└── purge_failed_parse_cache.py             # NEW: one-time cleanup utility for Issue #20

apps/api/tests/unit/evals/
└── test_judge_cache.py                     # NEW or MODIFIED: tests for cache validation + cleanup

docs/architecture/adr/
└── 0023-eval-rigor.md                      # NEW: ADR for judge consistency + cache poisoning decisions

CURRENT_STATE.md                            # MODIFIED: mark Issues #37, #21, #20 closed; add Part A audit summary
```

If Part A surfaces a production secret fix, the secret rotation itself happens out-of-band via `gcloud` (no code change). The doc updates land in CURRENT_STATE.md and possibly ADR-0021.

## Verification commands

```yaml
- name: backend-tests
  cmd: cd apps/api && pytest -v
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
- name: judge-cache-tests
  cmd: cd apps/api && pytest tests/unit/evals/test_judge_cache.py -v
  required: true
```

Pre-commit hooks must pass locally before any push.

## Subagent usage rules

- Use `executor` for code writing, file edits, ADR drafting, gcloud commands for audit
- Use `verifier` for tests/lint/types
- Each Part (A, B) ships as its own PR
- Part A audit work may be entirely operational (no code changes if all secrets check out) — in that case, only doc commits ship via PR

## Escalation rules (orchestrator MUST ask before doing)

**Standard rules:**
- Ask before installing any new dependency (none expected)
- Ask before triggering production deploys (staging is automatic; production requires explicit user approval)
- Ask if verification fails 3 times in a row on the same check
- Ask if any existing test newly fails after changes
- Ask if a single executor pass would touch more than 6 files

**Production audit-specific rules (Part A):**

- **STOP and escalate if ANY production secret value appears to be a placeholder.** GG performs the rotation manually (same pattern as iteration 1 Part A staging rotation). Subagents do not write to production secrets.
- **STOP and escalate if production cache backend logs show `in_memory` instead of `redis`.** This means production has the same silent fallback issue staging had.
- **STOP and escalate if any production Cloud Run service appears unreachable or in a degraded state.** Don't attempt to remediate; surface the finding.
- **STOP and escalate if the audit reveals secrets bound to services we didn't expect.** New finding — needs scope decision.

**Iteration scope expansion rule (the core Option A escalation):**

- **STOP and escalate if Part A audit reveals MORE THAN 2 production fixes are needed beyond the initial scope.** Two fixes is the tolerance; three or more means the iteration's scope has materially expanded and we need to decide whether to:
  - Defer Part B (eval rigor) to iteration 3
  - Defer some Part A fixes to a follow-up iteration
  - Continue with full scope if budget allows

The orchestrator should NOT silently absorb expanded scope. Surface and let GG decide.

**Eval rigor-specific rules (Part B):**

- Ask before changing nightly cron behavior (the cron is load-bearing — see Issue #15 for context on Llama TPD constraints)
- Ask if Issue #21 Approach 1 (pin judge, fail on TPD) would cause baseline runs to fail nightly cron — Approach 3 may be safer if so
- Ask if Issue #20 cleanup utility's purge logic could match legitimate entries (false-positive risk)
- Don't modify existing baseline thresholds or scoring algorithms — only add observability and validation

## Hard rules (DO NOT touch)

- `apps/api/src/travel_agent/api/routes/refine.py` — backend wiring locked
- `apps/api/src/travel_agent/api/routes/search.py` — production search endpoint
- `apps/api/config/llm_routing.yaml` — profile YAML is load-bearing
- The 4 demo profile names — don't rename
- All existing ADRs (0001-0022) — read-only references, create new ADR 0023 for this iteration's eval decisions
- `apps/api/evals/optimizer/thresholds.py` values — don't relax thresholds
- `apps/api/src/travel_agent/coordinator/streaming.py` event types — don't add or rename SSE event types
- `apps/api/src/travel_agent/agents/prompts/conversation_manager_system.txt` — system prompt was iterated to produce natural args_summary
- `apps/api/src/travel_agent/agents/optimizer.py` system prompt — has departure-time hallucination constraint
- `apps/api/src/travel_agent/api/cache.py` structured log event names — don't rename, only add new ones if needed
- PR #36 merge commit (41e8e659) — canonical Phase 2C.4.5 reference
- PR #38 merge commit (b872164) — canonical `[skip ci]` guardrail reference
- PRODUCTION SECRET VALUES — orchestrator may READ via `gcloud secrets versions access` (audit purpose), but NEVER WRITE/ROTATE. GG performs all production secret rotations manually.

## Budget

- **Soft target:** 1 Max plan 5-hour window for both Parts combined
- **Hard cap:** stop and escalate if executor invocations exceed 25 across the full iteration
- **Cost check:** orchestrator runs `/cost` after Part A completes (before Part B starts) and reports

Expected breakdown:
- Part A: ~3-5 executor invocations if audit is clean; ~8-12 if fixes needed
- Part B: ~10-12 executor invocations (Issue #21 + #20 + ADR + tests)

If Part A surfaces unexpected scope and Iteration Scope Expansion Rule fires, Part B may be deferred to iteration 3.

## Success criteria (orchestrator verifies ALL before declaring done)

**Part A — Production secret audit:**
- [ ] All production-bound GCP Secret Manager secrets audited (inventory + value pattern check)
- [ ] Production Cloud Run service bindings documented
- [ ] Production cache backend confirmed via `cache_backend_selected` logs (expecting `backend=redis`)
- [ ] Any placeholder/misconfigured secrets identified, escalated to GG, rotated, and verified
- [ ] CURRENT_STATE.md updated with audit summary
- [ ] Issue #37 closed with audit findings comment

**Part B — Eval rigor:**
- [ ] Issue #21 fix landed: judge selection logic per chosen Approach (1, 2, or 3)
- [ ] Issue #20 fix landed: cache validation rejects poisoned entries; cleanup utility exists
- [ ] ADR-0023 written documenting both decisions
- [ ] Unit tests pass for cache validation and cleanup
- [ ] One eval run executed post-fix to confirm baseline numbers stay in expected range
- [ ] Issues #21 and #20 closed
- [ ] PR opened, CI green, squash-merged
- [ ] Deploy — Staging succeeds on merge commit

**Both Parts:**
- [ ] Coverage stays ≥ 80%
- [ ] ruff + mypy clean
- [ ] No production deploys triggered
- [ ] No ANTHROPIC_API_KEY env var set
- [ ] No [skip ci] used in any commit message (would now be enforced by branch protection from iteration 1 Part B)
- [ ] No load-bearing file from Hard Rules section modified
- [ ] Production secrets never written by subagents — only GG via manual `gcloud secrets versions add`
- [ ] CURRENT_STATE.md updated to reflect Issues #37, #21, #20 closed

## Build order (recommended)

1. **Part A first.** Audit production secrets and verify cache backend. This is risk reduction — most important to complete even if Part B gets deferred.
2. **Pause and run `/cost` after Part A.** Report budget status. If Part A surfaced unexpected scope and triggered the Iteration Scope Expansion Rule, this is the decision point.
3. **Part B second.** Eval rigor work. Two issues (#21 and #20) in one PR (they're related).
4. **Each Part is a standalone PR.** Part A may be doc-only PR if no code changes needed. Part B is one PR covering both eval issues.

## Notes for the orchestrator

- Max plan covers Opus 4.7 (orchestrator) + Sonnet (executor/verifier) — never set `ANTHROPIC_API_KEY` env var
- `[skip ci]` discipline: do NOT use [skip ci] in any commit message. As of iteration 1 Part B, the workflow check is required and will block merges containing this tag.
- Production audit is fundamentally a READ-ONLY operation from the subagent perspective. Any WRITE to production secrets goes through GG.
- The eval rigor work touches the `evals/optimizer/` codebase which has been stable across Phase 2C. Don't rewrite or "improve" existing logic; only add the specific validations and selections described.
- Issue #37's body has context from iteration 1 Part A — read it via `gh issue view 37` before starting.
- Issues #21 and #20 are older (filed during Phase 2C arc) — read via `gh issue view 21` and `gh issue view 20` for original context.
- This iteration has higher risk than iteration 1 because Part A touches production. The escalation rules are stricter on purpose. When in doubt, escalate.
