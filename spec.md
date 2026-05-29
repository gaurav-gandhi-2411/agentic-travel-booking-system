# Project Spec: Phase 2D Iteration 1 — Observability + CI Hygiene

## Goal

Three operational fixes bundled into one iteration because they share the same shape — small CI/observability/instrumentation work, each with a clear definition of done. These are not feature work; they're the cleanup that should land before Phase 3 production hardening begins.

**Issue #31 — Cache backend observability.** The `_make_cache()` function uses `contextlib.suppress(Exception)` to fall back from Redis to in-memory cache without logging. We cannot tell from staging logs whether Redis is actually working. Add structured logging at the cache-backend selection point and on each cache operation. This unblocks all future cache debugging.

**Issue #30 — `[skip ci]` squash-merge guardrail.** When PR branches contain `[skip ci]` in any commit message (e.g., from ruff-format commits), squash-merging inherits the tag into the merge commit, which suppresses Deploy-Staging. Bit us on PR #27. Add a workflow that strips `[skip ci]` from squash commit messages before merge, OR add a pre-merge check that rejects squashes whose composed message would contain `[skip ci]`.

**Issue #18 — pip-audit 0s failure noise.** The pip-audit workflow reports failures with 0s duration on every push due to path filter quirks. Cumulative dashboard noise masks real audit failures. Fix the workflow to either remove path filters or add an explicit no-op skip job for unmatched paths.

Together: ~3-4 hours of orchestrator work, three small focused PRs, clean wins.

## Current state

See `CURRENT_STATE.md` for non-obvious context. Critical items relevant to this iteration:

- Phase 2C.4 and 2C.4.5 are complete. Top of main: 42a0d4a (CURRENT_STATE.md update post-PR-#36). PR #36 itself is at 41e8e65.
- Backend route at `apps/api/src/travel_agent/api/routes/refine.py` is locked — don't touch it.
- The `apps/api/src/travel_agent/api/cache.py` is the file to modify for Issue #31. It's load-bearing but the modification is additive (logging only).
- The GitHub Actions workflows live at `.github/workflows/`. Deploy-Staging triggers on push to main. The `[skip ci]` inheritance trap is documented in Issue #30's body.
- The pip-audit workflow has been flagging false failures since before Phase 2C began. Issue #18 has the root cause noted (path filter mismatch).
- All three issues are open. Read each issue body in full via `gh issue view <N>` before planning the fix.

## Scope

### In scope (this iteration)

**Part A — Issue #31 cache observability:**

- Add structured logging to `apps/api/src/travel_agent/api/cache.py`:
  - `cache_backend_selected` event at end of `_make_cache()`: backend="redis" | "in_memory", and on fallback, error_class + error_message
  - `cache_init_fallback` event inside the `contextlib.suppress(Exception)` before swallowing — captures why fallback fired
  - `search_cache_put_success` event after `RedisSearchCache.put()` completes
  - `search_cache_get_result` event after `RedisSearchCache.get()` — include `hit=True|False`
  - Every cache log must include the Cloud Run revision identifier (`K_REVISION` env var, fallback to hostname)

- Add unit tests for each log emission. Use existing test patterns in `tests/unit/api/test_cache.py` (or create if missing).

- Update CURRENT_STATE.md's "Known issues" section to note that Issue #31 is now closed.

- Verify on staging after deploy: trigger a `/search` curl, then check Cloud Run logs for the new structured events. Cache backend selection should now be observable.

**Part B — Issue #30 [skip ci] guardrail:**

Two viable approaches. Orchestrator picks one based on which is simpler:

- **Approach 1 (preferred):** GitHub Actions workflow that strips `[skip ci]` from squash commit messages. Requires understanding GitHub's commit message composition for squash merges and intercepting before push.

- **Approach 2 (fallback):** Branch protection rule or pre-merge status check that rejects squashes whose composed message contains `[skip ci]`. Requires repo admin action (may need GG).

The deliverable: a working mechanism that prevents the silent deploy suppression. Verified by either (a) merging a test PR containing `[skip ci]` in a commit and observing the merge commit either has the tag stripped or the merge was rejected, OR (b) showing the workflow change with documentation of expected behavior.

**Part C — Issue #18 pip-audit noise:**

- Fix `.github/workflows/pip-audit.yml` (or equivalent) so it doesn't produce 0s failures when paths don't match.
- Options: remove path filters and let the job exit early via internal skip logic, OR add an explicit `if:` condition that gates the audit step.
- Verified by triggering CI on a no-Python-changes commit and observing pip-audit either succeeds with skip or doesn't run at all.

### Out of scope (do not build)

- Phase 2D issues NOT listed above: #14, #15, #20, #21, #29, #33, #34, #35 — separate iterations
- Phase 3 work (real hotel data, BookingAgent, multi-tenancy)
- Any modification to the cache backend itself (only logging added; behavior unchanged)
- Any modification to deploy workflows beyond [skip ci] guardrail
- Any code execution against the production environment
- New features, new agents, new SSE event types
- Changing existing log formats or removing existing logs
- Modifying `apps/api/src/travel_agent/api/routes/refine.py` or any other HARD RULE file from CURRENT_STATE.md

## Tech stack

Only what's relevant:

- Python 3.12 (backend)
- structlog or similar (existing logging pattern — check `apps/api/src/travel_agent/observability/` for the convention)
- pytest (testing)
- GitHub Actions YAML (workflow changes)

No new dependencies. If anything needs adding, escalate.

## Architecture

NEW or MODIFIED files this iteration:

```
apps/api/src/travel_agent/api/
├── cache.py                              # MODIFIED: Add 4 structured logging events + K_REVISION attribution
└── (no other files)

apps/api/tests/unit/api/
├── test_cache.py                         # NEW or MODIFIED: Tests for new logging events

.github/workflows/
├── deploy-staging.yml                    # MODIFIED: [skip ci] strip logic (Part B Approach 1)
│                                         # OR no change (Part B Approach 2 — branch protection)
└── pip-audit.yml                         # MODIFIED: Path filter or skip logic (Part C)

docs/architecture/adr/
└── 0021-cache-observability.md           # NEW: Brief ADR for Part A's logging schema
```

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
- name: workflow-yaml-lint
  cmd: yamllint .github/workflows/
  required: false
```

Pre-commit hooks must pass locally before any push.

## Subagent usage rules

- Use `executor` for code writing, file edits, ADR drafting
- Use `verifier` for tests/lint/types
- Each Part (A, B, C) ships as its own PR — don't bundle multiple Parts into one PR
- Each PR gets its own staging deploy verification before declaring its Part done

## Escalation rules (orchestrator MUST ask before doing)

- Ask before installing any new dependency (none expected)
- Ask if cache.py logging additions break any existing test or change existing log output format
- Ask if Part B Approach 1 (workflow-based [skip ci] strip) turns out to require admin permissions GG must grant — escalate to ask for the alternative Approach 2
- Ask if Part B requires repo settings changes (branch protection) that orchestrator/CC can't make directly
- Ask if any Part causes other workflows to fail (e.g., the [skip ci] strip somehow affects unrelated workflows)
- Ask if verification fails 3 times in a row on the same check
- Ask if any existing test newly fails after Part A logging changes
- Ask if a single executor pass would touch more than 6 files
- Ask before triggering production deploys (staging is automatic; production requires explicit user approval)

## Hard rules (DO NOT touch)

- `apps/api/src/travel_agent/api/routes/refine.py` — backend wiring locked
- `apps/api/src/travel_agent/api/routes/search.py` — production search endpoint
- `apps/api/config/llm_routing.yaml` — profile YAML is load-bearing
- The 4 demo profile names — don't rename
- All existing ADRs (0001-0020) — read-only references, create new ADR (0021) for this iteration's decisions
- `apps/api/evals/optimizer/thresholds.py` — don't relax thresholds
- `apps/api/src/travel_agent/coordinator/streaming.py` event types — don't add or rename SSE event types
- `apps/api/src/travel_agent/agents/prompts/conversation_manager_system.txt` — system prompt was iterated to produce natural args_summary
- `apps/api/src/travel_agent/agents/optimizer.py` system prompt — has departure-time hallucination constraint
- Existing structured log event names — don't rename them in cache.py; add new ones, don't change existing
- PR #36 merge commit: 41e8e659... — canonical "Phase 2C.4.5 complete" reference

## Budget

- **Soft target:** 1 Max plan 5-hour window for all three Parts combined
- **Hard cap:** stop and escalate if executor invocations exceed 20 across the full iteration
- **Cost check:** orchestrator runs `/cost` after Part A merges (midpoint) and reports

Expected breakdown:
- Part A: ~5-7 executor invocations (cache.py logging + tests + ADR-0021 + staging verify)
- Part B: ~3-4 executor invocations (workflow change + test PR + verify)
- Part C: ~2-3 executor invocations (workflow fix + verify on next CI run)

## Success criteria (orchestrator verifies ALL before declaring done)

**Part A — Cache observability:**
- [ ] cache.py emits `cache_backend_selected`, `cache_init_fallback`, `search_cache_put_success`, `search_cache_get_result` events
- [ ] Every cache log includes K_REVISION (or hostname fallback)
- [ ] Unit tests pass for each log emission
- [ ] ADR-0021 written documenting the logging schema
- [ ] PR opened, CI green, squash-merged
- [ ] Deploy — Staging succeeds on merge commit
- [ ] Manual verification: curl /search against staging, then run `gcloud logging read` with the new event names — should see structured logs from the actual request
- [ ] CURRENT_STATE.md's "Known issues" section updated to mark Issue #31 closed

**Part B — [skip ci] guardrail:**
- [ ] Mechanism in place (workflow strip OR branch protection) that prevents [skip ci] from suppressing deploys after squash merges
- [ ] Verification: either (a) test PR with [skip ci] in a commit message either has the tag stripped on squash OR the squash is rejected, OR (b) documented behavior with clear test plan
- [ ] PR opened, CI green, merged
- [ ] Deploy — Staging succeeds on merge commit

**Part C — pip-audit noise:**
- [ ] pip-audit no longer reports 0s failures on no-Python-change commits
- [ ] Either workflow runs successfully and exits cleanly, OR doesn't run when paths don't match
- [ ] Verified on a subsequent commit to main after merge
- [ ] PR opened, CI green, merged

**All Parts:**
- [ ] Coverage stays ≥ 80%
- [ ] ruff + mypy clean
- [ ] No production deploys triggered
- [ ] No ANTHROPIC_API_KEY set
- [ ] No [skip ci] used in any commit message in this iteration
- [ ] No load-bearing file from Hard Rules section modified
- [ ] Each Part shipped as its own PR (three PRs total)
- [ ] CURRENT_STATE.md updated to reflect Issues #31, #30, #18 closed

## Build order (recommended)

1. Part A first — it's the highest-value fix (unblocks future cache debugging) and the most code-touching of the three
2. Part B second — depends on Part A only for sequencing (clean main state)
3. Part C third — smallest scope; quickest win to close out the iteration

Each Part is a standalone PR. Open Part A's PR, merge, deploy verify. Then start Part B. Then Part C. Don't open multiple PRs in parallel — sequential prevents merge conflicts and keeps the staging deploy chain clean.

## Notes for the orchestrator

- Max plan covers Opus 4.7 (orchestrator) + Sonnet (executor/verifier) — never set `ANTHROPIC_API_KEY` env var
- `[skip ci]` discipline: do NOT use [skip ci] in any commit message in this iteration. Part B is specifically about preventing the squash-merge inheritance trap; using it would be ironic and self-defeating.
- Each issue (#31, #30, #18) has a filed GitHub issue with body context. Read `gh issue view <N>` for each before planning the fix.
- Issue #29 (Groq schema case sensitivity) is OUT OF SCOPE. The two-layer fix already landed in PR #27.
- This iteration uses smaller subagent budget than Phase 2C.4.5 — the work is more bounded.
