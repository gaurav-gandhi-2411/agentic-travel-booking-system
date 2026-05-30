# Project Spec: Phase 2D Iteration 3 — Production Deploy (v0.5.0 → current main)

## Goal

Production has been frozen at `v0.5.0` (commit 78c57db, 2026-05-15) since before Phase 2B. It is missing the entire Redis cache layer, Langfuse observability, structured logging, all of Phase 2C (ConversationManagerAgent, /refine rewiring, SSE event changes, 4-profile demo), and all of Phase 2D. Staging is current; production is ~three phases behind.

This iteration brings production to current main — but deliberately, in two phases:

**Phase 1 — De-risk (investigate + canary).** Verify staging/production config parity, confirm the canary → promotion mechanism in `deploy-prod.yml` actually works (it has not been exercised since May 15), deploy a canary revision serving 0% traffic, and smoke-test it before any real traffic touches it.

**Phase 2 — Promote.** Only after the canary smoke test passes, promote to 100% traffic with an explicit rollback plan ready.

The reason for two phases: this arc has surfaced four consecutive "assumed-working-but-wasn't" findings. A blind 100% production promotion after two weeks of drift is exactly the failure mode to avoid. Verify the path before trusting it.

## Current state

See `CURRENT_STATE.md`, especially the "Production audit summary" subsection added in iteration 2. Critical facts:

- Production service: `agentic-travel-booking-api-prod`, region asia-south1, currently running v0.5.0 image. Service URL: https://agentic-travel-booking-api-prod-646079085526.asia-south1.run.app
- Env bindings on the prod service were corrected in iteration 2 (APP_ENV=production, UPSTASH_REDIS_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY) — they are correct but inert against the v0.5.0 image.
- `deploy-prod.yml` was corrected in PR #43 (merge 42f1683). The workflow fires only on `push: tags: v*` or `workflow_dispatch` — NOT on merge to main.
- The workflow does a canary → 100% promotion pattern. This path has not run since 2026-05-15.
- Staging auto-deploys on every push to main and is current. Staging is the reference for "what a working current deployment looks like."
- Issue #44 (this iteration) has the pre-flight checklist context. Issue #45 tracks the staleness process gap (separate, lower priority).
- `UPSTASH_REDIS_TOKEN` is vestigial — no code references it. Do not bind it.
- Production runs `APP_MODE=demo` and `LLM_ROUTING_PROFILE=demo` (Haiku/Anthropic path). OpenRouter is bound for on-demand `free` profile activation but not used by default.

## Scope

### In scope (this iteration)

**Phase 0 — Discovery (read-only, report before proceeding):**

1. Confirm the exact delta between v0.5.0 and current main:
   - `git log --oneline v0.5.0..main | wc -l` (commit count)
   - `git log --oneline v0.5.0..main` (the actual commits, summarized by phase)
   - Confirm the current main HEAD SHA and whether it's the intended deploy target

2. Staging/production config parity audit:
   - Full env var + secret binding comparison between `agentic-travel-booking-api-staging` and `agentic-travel-booking-api-prod`
   - Document every difference. Classify each as: intended (e.g., APP_ENV value differs), or accidental (e.g., a secret bound in staging but missing in prod)
   - Specifically verify: does prod have every secret binding that staging has, accounting for the iteration-2 fixes?

3. Inspect the `deploy-prod.yml` workflow mechanics:
   - How does it build the image? (Cloud Build, Docker, Artifact Registry path)
   - How does the canary step work? (traffic split percentage, tag-based revision, `--no-traffic` flag)
   - How does promotion to 100% work? (manual gate, automatic after canary, separate workflow_dispatch)
   - Is there a rollback mechanism, or is rollback manual (`gcloud run services update-traffic --to-revisions`)?
   - What triggers it: tag push (`v*`) or workflow_dispatch or both?

4. Confirm image build will succeed:
   - Check the Dockerfile and build context are current
   - Confirm Artifact Registry repo (`travel-agent`, asia-south1) is accessible
   - Check whether the WIF service account has the permissions the deploy needs

**PAUSE after Phase 0.** Report all findings. GG + external engineer review the parity audit and the workflow mechanics before any deploy action. The discovery may reveal that the canary path needs a fix before it can be used, or that there's a config difference that must be reconciled first.

**Phase 1 — Canary deploy (after Phase 0 review):**

5. Decide the version tag (recommend `v0.6.0` given the multi-phase delta — confirm with GG).

6. Trigger the production deploy to a CANARY revision serving 0% traffic:
   - Either via tag push or workflow_dispatch (per Phase 0 findings on what the workflow supports)
   - The canary revision should be deployed but NOT serving traffic (`--no-traffic` or equivalent)
   - If the workflow auto-promotes to 100% without a canary gate, STOP — we need to modify the workflow to add a manual gate before using it (escalate)

7. Smoke-test the canary revision directly (canary revisions get a unique URL):
   - GET /health → expect {"status":"ok","phase":"C","cache":"ok"} (the cache field that v0.5.0 lacked)
   - cache_backend_selected logs → backend=redis
   - POST /search → full SSE pipeline, capture request_id
   - search_cache_put_success log appears
   - POST /refine with request_id → REFINE classification, archetypes, cache hit, NOT "Session expired"
   - search_cache_get_result hit=true
   - Structured JSON logs appearing
   - Confirm the 4 demo profiles are selectable (demo-llama, demo-gpt-oss-120b, demo-deepseek-v4, demo-haiku)

**PAUSE after Phase 1.** Report canary smoke-test results. GG decides go/no-go on promotion. Do NOT promote without explicit GG approval.

**Phase 2 — Promote (after Phase 1 review + GG go-ahead):**

8. Promote the canary to 100% traffic:
   - Via the workflow's promotion mechanism, or `gcloud run services update-traffic --to-latest` / `--to-revisions=<canary>=100`

9. Post-promotion verification (against the main service URL now serving the new revision):
   - Repeat the smoke test (health, cache, /search, /refine) against the production URL
   - Confirm 100% traffic on the new revision via `gcloud run services describe ... --format="value(status.traffic)"`

10. Rollback readiness: document the exact rollback command before promoting, so it's ready if post-promotion verification fails:
    - `gcloud run services update-traffic agentic-travel-booking-api-prod --region asia-south1 --to-revisions=<v0.5.0-revision>=100`
    - Identify the v0.5.0 revision name during Phase 0 so the rollback target is known

**Phase 3 — Close-out:**

11. Update CURRENT_STATE.md: production now on current main (tag), v0.5.0 freeze resolved, the iteration-2 env bindings are now active.
12. Close Issue #44 with the deploy summary (version deployed, smoke-test results, traffic confirmation).
13. Reference Issue #45 (staleness guardrail) as the remaining follow-up — do NOT implement it this iteration unless trivial.

### Out of scope (do not build)

- Issue #45 staleness guardrail implementation (separate iteration; just reference it)
- Part B eval rigor (Issues #20, #21 — deferred to a later iteration)
- Phase 3 features (real hotel data, BookingAgent, multi-tenancy)
- Any code changes beyond what's strictly needed to make the deploy succeed (this is a deploy iteration, not a feature iteration)
- Modifying agent prompts, eval thresholds, or any HARD RULE file
- Changing the canary/promotion mechanism unless Phase 0 reveals it's broken and must be fixed to proceed (escalate first)
- Frontend deploy (production Vercel frontend — separate concern; flag if it needs a corresponding update but don't do it here)

## Tech stack

- Cloud Run, Artifact Registry (asia-south1), Cloud Build
- GitHub Actions (deploy-prod.yml), WIF auth
- gcloud CLI
- Docker (for image build, handled by the workflow)

No application code changes expected. If the deploy requires a code or Dockerfile fix, escalate before making it.

## Architecture

This iteration primarily touches deployment infrastructure, not application code. Files potentially modified:

```
.github/workflows/
└── deploy-prod.yml        # ONLY if Phase 0 reveals the canary/promotion path is broken (escalate first)

docs/architecture/adr/
└── 0023-production-deploy.md   # NEW: ADR documenting the v0.5.0→current deploy, canary verification, rollback plan

CURRENT_STATE.md           # MODIFIED: production now current, freeze resolved
```

If Phase 0 reveals a code/Dockerfile issue blocking the build, that's a surface expansion — escalate and re-plan.

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
```

These verify current main is healthy before deploying it. The real verification for this iteration is the canary smoke test (Phase 1), not unit tests.

## Subagent usage rules

- Use `executor` for gcloud commands, workflow inspection, ADR drafting, doc updates
- Use `verifier` for the pre-deploy test/lint/type run on current main
- The deploy trigger itself (tag push / workflow_dispatch) and traffic promotion are PRODUCTION ACTIONS — see escalation rules; GG approves each phase gate

## Escalation rules (orchestrator MUST ask before doing)

**Production-action gates (the core escalations):**
- STOP after Phase 0. Report parity audit + workflow mechanics. Do NOT trigger any deploy until GG + external engineer review.
- STOP after Phase 1 canary. Report smoke-test results. Do NOT promote to 100% without explicit GG go-ahead.
- STOP before any action that would put a new revision in front of production traffic. Canary at 0% is fine to deploy after Phase 0 review; promotion to 100% needs the Phase 1 gate.

**Discovery-driven escalations:**
- STOP if Phase 0 reveals the canary path auto-promotes to 100% with no gate — we'd need to modify the workflow first.
- STOP if the staging/prod parity audit reveals a config difference that would make the deploy behave differently than staging (e.g., a missing secret, a different APP_MODE).
- STOP if the image build fails for any reason — diagnose, surface, don't retry blindly.
- STOP if Phase 0 reveals the WIF service account lacks deploy permissions.
- STOP if current main HEAD has any failing CI or uncommitted-looking state.

**Standard:**
- Never set ANTHROPIC_API_KEY
- No [skip ci] in any commit (required check blocks it)
- Ask before installing dependencies
- Ask if verification fails 3 times on the same check
- Ask if a single executor pass would touch more than 6 files

## Hard rules (DO NOT touch)

- All application code in `apps/api/src/travel_agent/` — this is a deploy iteration, no app code changes
- All HARD RULE files from prior specs (refine.py, search.py, llm_routing.yaml, prompts, thresholds, streaming event types)
- The 4 demo profile names
- All existing ADRs (0001-0022) — create new ADR 0023
- Production secrets — GG handles any rotation; subagents read-only
- Do NOT promote canary to 100% without the Phase 1 gate passing and GG approving
- Do NOT trigger the prod deploy during Phase 0 (discovery is read-only)

## Budget

- Soft target: 1 Max plan window for Phase 0 + Phase 1; Phase 2 promotion is fast once approved
- Hard cap: escalate if executor invocations exceed 20
- Cost check: /cost after Phase 0 (before any deploy action) and again at close-out

## Success criteria (orchestrator verifies ALL before declaring done)

**Phase 0:**
- [ ] v0.5.0→main commit delta documented
- [ ] Staging/prod config parity audit complete, all differences classified
- [ ] deploy-prod.yml mechanics documented (build, canary, promotion, rollback, trigger)
- [ ] Image build feasibility confirmed (Dockerfile current, Artifact Registry accessible, WIF permissions sufficient)
- [ ] v0.5.0 revision name identified (for rollback target)
- [ ] Reported and reviewed before any deploy

**Phase 1:**
- [ ] Version tag chosen (recommend v0.6.0, GG confirms)
- [ ] Canary revision deployed at 0% traffic
- [ ] Canary smoke test: health shows cache:ok, cache_backend_selected=redis, /search works, /refine works with cache hit, structured logs appear, 4 profiles selectable
- [ ] Reported and reviewed; GG go-ahead obtained before promotion

**Phase 2:**
- [ ] Canary promoted to 100% traffic
- [ ] Post-promotion smoke test passes against production URL
- [ ] 100% traffic confirmed on new revision
- [ ] Rollback command documented and ready (not needed if verification passes)

**Phase 3:**
- [ ] CURRENT_STATE.md updated — production current, freeze resolved
- [ ] Issue #44 closed with deploy summary
- [ ] ADR-0023 written (deploy narrative, canary verification, rollback plan)
- [ ] Issue #45 referenced as remaining follow-up

**All phases:**
- [ ] No app code changed (deploy-only iteration)
- [ ] No ANTHROPIC_API_KEY set
- [ ] No [skip ci] used
- [ ] Each production-action gate respected (Phase 0 review, Phase 1 go-ahead)
- [ ] Rollback target known before promotion

## Build order

1. Phase 0 discovery (read-only) → PAUSE, report, review
2. Phase 1 canary deploy at 0% → smoke test → PAUSE, report, GG go-ahead
3. Phase 2 promote to 100% → post-promotion verify
4. Phase 3 docs + close-out

## Notes for the orchestrator

- Max plan covers Opus 4.7 + Sonnet — never set ANTHROPIC_API_KEY
- This is the highest-risk iteration so far: it puts ~3 phases of unreleased work in front of production traffic. The two-gate structure (Phase 0 review, Phase 1 go-ahead) is deliberate. When in doubt, pause and ask.
- The canary-first approach exists because this arc has had four consecutive "assumed-working-but-wasn't" findings. Verify the deploy path on a 0%-traffic canary before trusting it with real traffic.
- Read Issue #44 via `gh issue view 44` — it has the pre-flight checklist context from iteration 2.
- The production Vercel frontend points at the production backend. After the backend deploy, the frontend may need to be checked for compatibility (new SSE event types, etc.) — flag this as a follow-up if relevant but the frontend deploy is out of scope here.
- If anything about the deploy path looks broken or ambiguous during Phase 0, surfacing it is the right move — a broken canary path discovered during Phase 0 is a much better outcome than a failed 100% promotion.
