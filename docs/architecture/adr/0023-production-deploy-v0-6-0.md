# ADR-0023 — Production Deploy: v0.5.0 → v0.6.0 (Phase 2D Iteration 3)

## Context

Production was frozen at `v0.5.0` (commit `78c57db`, tagged 2026-05-15) since before Phase 2B merged. The freeze originated from a mid-May canary deploy attempt (revision `00012-mab`, created 2026-05-16) that stopped after the `no_traffic` step without promoting — leaving the `canary` tag on a pre-Phase-2B image at 0% traffic and the v0.5.0 revision at 100%.

By the time Phase 2D iteration 3 began (2026-05-30), production was 69 commits behind `main`, missing:

| Phase | Missing features |
|-------|-----------------|
| Phase 2B | Redis cache (`cache.py`, `redis_cache.py`), Langfuse observability, structured JSON logging via JSONRenderer |
| Phase 2C | ConversationManagerAgent, `/refine` endpoint, 4-profile demo set (GPT-OSS-120B, DeepSeek V4 Flash, Llama, Haiku), SSE protocol changes, prompt caching |
| Phase 2D.1 | Cache backend observability log events, `[skip ci]` guardrail |
| Phase 2D.2 | `APP_ENV=production` in deploy workflow, env bindings correction (PR #43) |

The env bindings for `UPSTASH_REDIS_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `APP_ENV=production` had been applied manually to the Cloud Run service in iteration 2 and were correct but inert against the v0.5.0 image.

A pre-flight audit (Phase 0) revealed that `deploy-prod.yml` had no manual gate: once triggered, it ran build → 0% canary → 5% traffic shift → 5-minute soak → 100% promotion in a single uninterrupted run. The soak's only check was `/health` (liveness), which would pass even if `/refine`, the Redis cache, or structured logging were broken.

## Decision

Deploy in two explicitly gated phases using a two-dispatch approach:

**Gate 1 (Phase 0 → Phase 1):** Audit staging/production config parity, inspect workflow mechanics, identify rollback target, and report before any deploy action. An external review of the parity audit and workflow findings was required before proceeding.

**Workflow fix (pre-Phase 1):** Add a `stage` input (`canary` | `full`) to `workflow_dispatch` (PR #46, commit `3d30839`). `stage=canary` builds the image and deploys at 0% traffic, then stops. `stage=full` skips the build (canary already running) and runs traffic shift → soak → 100% promotion. Tag push (`v*`) retains the full pipeline. This separates the liveness-only automated soak from the human correctness gate.

**Gate 2 (Phase 1 → Phase 2):** Deploy canary at 0% traffic via `workflow_dispatch stage=canary`, smoke-test it directly at the `canary---` URL (correctness: `/health` + `/search` + `/refine` + cache hit + structured logs + all 4 profiles), and report results before promoting.

**Phase 2:** Promote via `workflow_dispatch stage=full` only after Gate 2 passed.

## Consequences

**Positive:**
- Production brought current with 69 commits and ~3 phases of unreleased work in a single controlled deployment.
- All correctness criteria verified against the canary revision before any real traffic touched it.
- The `/refine` + cache hit cycle (Phase 2C core feature) confirmed working on prod infra: `search_cache_get_result hit=true` against `UPSTASH_REDIS_URL` bound to the running image.
- `cache:ok` on `/health` confirmed the iteration-2 env bindings (previously inert) are now connected to code that reads them.
- Structured JSON logging (`jsonPayload` in Cloud Logging) confirmed for all request-time events.
- All 4 demo profiles (demo-llama, demo-gpt-oss-120b, demo-deepseek-v4, demo-haiku) confirmed working end-to-end on prod infra.
- The manual gate pattern is now encoded in `deploy-prod.yml` and will be available for all future deploys.

**Negative / observations:**
- `cache_backend_selected` logs as `textPayload` (ConsoleRenderer) at worker startup rather than `jsonPayload` (JSONRenderer). The event fires during each uvicorn worker's startup sequence before the FastAPI lifespan context that configures JSONRenderer. All request-time events use JSONRenderer correctly. Tracked as a low-priority follow-up (Issue #47).
- The `canary` revision tag persists on `00016-rab` after 100% promotion (Cloud Run retains tags on revisions indefinitely). This is cosmetically redundant but harmless.

## Alternatives

**Alternative 1 — Blind 100% promotion via tag push.** Rejected: the arc had four consecutive "assumed-working-but-wasn't" findings (placeholder Redis URL, em-dash YAML, unapplied prod secret, frozen prod). The risk of a 3-phase delta promoting straight to 100% with only a liveness soak was too high.

**Alternative 2 — Split into two separate workflow files.** Considered but not needed; the `stage` input on a single workflow provides the same gate with less file-system overhead.

**Alternative 3 — GitHub Environment approval gate.** The `production` environment already has a required-reviewer protection rule (`gaurav-gandhi-2411`). This provided a second layer of human approval at the job level (the workflow paused for environment approval before any step ran). The `stage` input gate operates at the step level and is complementary, not redundant.

## Rollback plan (documented pre-promotion, not needed)

```bash
gcloud run services update-traffic agentic-travel-booking-api-prod \
  --region=asia-south1 \
  --to-revisions=agentic-travel-booking-api-prod-00011-2k8=100
```

v0.5.0 revision `00011-2k8` remains available in Cloud Run history for rollback if needed.
