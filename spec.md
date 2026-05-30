# Project Spec: Phase 2D Iteration 5 — Staleness Guardrail + CI Housekeeping

## Goal

Four CI/process-hygiene items bundled because they share the same shape — small, bounded, no application logic. The anchor is the staleness guardrail; the rest are accumulated housekeeping that fits the same iteration cleanly (precedent: iteration 1 bundled observability + CI hygiene successfully).

**Issue #45 — Production staleness guardrail (the anchor).** Both production surfaces silently froze for two weeks: the Cloud Run backend at v0.5.0 (fixed iteration 3) and the Vercel frontend at commit 034bc03 with empty env vars (fixed iteration 4). Same root cause both times — auto-deploy not firing, nobody noticing. Build a scheduled check that detects when EITHER production surface drifts behind main and alerts (does NOT auto-deploy — deploys stay deliberate and gated). This is the guardrail that prevents the entire iteration-3/4 remediation from being necessary again.

**Issue #41 — setup-node v4 → v5.** `actions/setup-node@v4` deprecates Sept 2026. Bump to v5 in the workflows that use it.

**Issue #42 — Migrate backlog.md to GitHub issues.** `apps/api/docs/backlog.md` accumulated items that were never promoted to formal issues. Catalog, migrate to GitHub issues, delete the file.

**Issue #47 — Startup-log renderer quirk (low priority).** `cache_backend_selected` logs as textPayload (ConsoleRenderer) at worker startup because it fires before the FastAPI lifespan configures JSONRenderer. Benign — fields are still parseable — but inconsistent with request-time jsonPayload logging. Fix if cheap; defer if it requires restructuring startup.

Together: ~3-4 hours. Each ships as its own PR (or #41/#47 may combine if trivial).

## Current state

See `CURRENT_STATE.md` (current through iteration 4). Critical facts:

- Both production surfaces are now CURRENT and functional (first time ever):
  - Backend: Cloud Run `agentic-travel-booking-api-prod`, running v0.6.0 (commit 3d30839), Redis cache active, 100% traffic. Canonical URL: `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`
  - Frontend: Vercel `agentic-travel-booking-system.vercel.app`, running current main (deploy dpl_F3DMy1YysATzBgWKBpd6RzoCvR85), env vars correct.
- Backend deploys via `.github/workflows/deploy-prod.yml`: fires on `push: tags: v*` or `workflow_dispatch`. Has a manual canary gate (stage=canary|full, added iteration 3 PR #46) AND a GitHub environment protection rule requiring GG approval. Deploys are DELIBERATE and GATED — the guardrail must not undermine this.
- Frontend deploys via Vercel CLI manually (`vercel deploy --prod --archive=tgz` from repo root). Vercel's GitHub auto-deploy is NOT firing (root cause dashboard-only, unconfirmed — folded into #45). Frontend deploys are currently manual.
- How to determine what commit each production surface runs:
  - Backend: the running Cloud Run revision's image was built with a commit SHA. The deploy workflow tags images `$IMAGE:$TAG` + `$IMAGE:${{ github.sha }}`. The running revision/image SHA is queryable via `gcloud run services describe` + image inspection, OR the v0.6.0 git tag points at 3d30839.
  - Frontend: the Vercel production deployment's source commit. Queryable via `vercel ls` / `vercel inspect` (CLI is authed as gaurav-gandhi-2411) or the Vercel API.
- Existing workflows live in `.github/workflows/`. There's already a scheduled nightly cron (eval golden set) — the staleness check can follow that cron pattern.
- `[skip ci]` guardrail (iteration 1, PR #38) is active as a required check. Don't trip it.
- `apps/api/docs/backlog.md` exists with un-migrated items.

## Scope

### In scope (this iteration)

**Part A — Issue #45 staleness guardrail (detect-and-alert, both surfaces):**

1. Create a scheduled GitHub Actions workflow `.github/workflows/production-staleness-check.yml`:
   - Runs on a cron schedule (recommend daily; align with or near the existing nightly cron)
   - Also runnable via `workflow_dispatch` for on-demand checks
   - Checks BACKEND drift: determine the commit the running Cloud Run prod revision was built from, compare against `main` HEAD. Compute commits-behind.
   - Checks FRONTEND drift: determine the commit the Vercel production deployment was built from, compare against `main` HEAD. Compute commits-behind.
   - ALERT behavior (NOT auto-deploy): if either surface is behind main beyond a threshold (recommend: behind by ≥1 commit that touches the relevant app dir — `apps/api/` for backend, `apps/web/` for frontend — OR simply ≥1 commit at all; decide during build), open or update a GitHub issue titled "⚠️ Production staleness detected" with the drift details (which surface, how many commits behind, the deployed SHA vs main HEAD, a link to the diff). Use a stable issue title so repeated runs update one issue rather than spamming new ones.
   - The check MUST NOT trigger any deploy. Detect and alert only.

2. Auth/access the workflow needs:
   - Backend check: needs gcloud auth in CI. The WIF service account (`travel-agent-deployer@...`) already authenticates the deploy workflow — reuse the same WIF setup for read-only `gcloud run services describe`.
   - Frontend check: needs to query Vercel. Either a Vercel API token (stored as a GitHub secret) or the Vercel deployment's git metadata via the Vercel REST API. Determine the cleanest path during build; if it needs a `VERCEL_TOKEN` secret, flag that GG must create it (GG action — generate token in Vercel account settings, add as GitHub repo secret).

3. Test the workflow:
   - Run it via workflow_dispatch against current state. Since both surfaces are currently CURRENT (iterations 3+4), the expected result is "no drift detected, no alert."
   - To verify the detection logic actually fires, the orchestrator can test the comparison logic against a known-stale input (e.g., temporarily compare against an old SHA) without actually leaving production stale — confirm the alert path works, then confirm the live check shows green.

4. Document the guardrail in an ADR and CURRENT_STATE.md.

**Part B — Issue #41 setup-node v4 → v5:**

5. Find all workflows using `actions/setup-node@v4`:
   `grep -rn "setup-node@v4" .github/workflows/`
6. Bump to `@v5`. Verify the workflows still pass (the Web CI job uses Node — confirm it builds/lints/typechecks green on v5).

**Part C — Issue #42 backlog.md migration:**

7. Read `apps/api/docs/backlog.md`. Catalog each item.
8. For each item not already a GitHub issue: file it as a GitHub issue with appropriate tags. For items already covered by existing issues: note the mapping, don't duplicate.
9. Delete `backlog.md` once migrated. Update any references to it.

**Part D — Issue #47 startup-log renderer quirk:**

10. Investigate: can `cache_backend_selected` (and any other startup-time structlog events) use JSONRenderer? The event fires during worker startup, before the FastAPI lifespan configures the renderer. Options:
    - Configure structlog's JSONRenderer earlier (at module import / before the startup log fires) so startup logs are also JSON
    - OR accept the textPayload startup logs as benign and just document the distinction (close #47 as wontfix-documented)
11. If the fix is cheap (move the structlog config earlier without side effects), do it. If it risks reordering startup or has side effects, document and close as low-priority-accepted. Escalate the judgment call if unclear.

### Out of scope (do not build)

- Auto-deploy on staleness detection (explicitly rejected — deploys stay deliberate/gated; the guardrail alerts only)
- Fixing the Vercel GitHub auto-deploy root cause (dashboard investigation — note in #45, don't solve here unless trivial and dashboard-accessible)
- Eval rigor #20/#21 (iteration 6)
- Any application logic changes (backend or frontend) — except the #47 startup-log config if it's a safe, contained change
- Any production deploy (this iteration adds monitoring, doesn't deploy)
- Phase 3 features
- Modifying the deploy-prod.yml deploy logic, the canary gate, or env protection (those are correct as-is)

## Tech stack

- GitHub Actions YAML (workflows)
- gcloud CLI (backend drift check, via existing WIF)
- Vercel REST API or CLI (frontend drift check)
- Python 3.12 + structlog (only for #47 if fixed)
- gh CLI (issue creation for #42 migration)

No new app dependencies. A `VERCEL_TOKEN` GitHub secret may be needed (GG creates).

## Architecture

```
.github/workflows/
├── production-staleness-check.yml      # NEW: scheduled drift detection, both surfaces, alert-only
└── *.yml (setup-node@v4 → @v5)         # MODIFIED: version bump in affected workflows

apps/api/docs/
└── backlog.md                          # DELETED after migration to issues

apps/api/src/travel_agent/
└── (observability/logging config)      # MODIFIED only if #47 startup-log fix is safe

docs/architecture/adr/
└── 0025-staleness-guardrail.md         # NEW: guardrail design, alert-not-deploy rationale

CURRENT_STATE.md                        # MODIFIED: guardrail documented, #45/#41/#42/#47 closed
```

## Verification commands

```yaml
- name: workflow-yaml-lint
  cmd: yamllint .github/workflows/ || true
  required: false
- name: backend-tests
  cmd: cd apps/api && pytest -v
  required: true
- name: backend-lint
  cmd: cd apps/api && ruff check .
  required: true
- name: backend-types
  cmd: cd apps/api && mypy src
  required: true
- name: frontend-build
  cmd: cd apps/web && npm run build
  required: true
```

(backend tests/lint/types + frontend build verify nothing broke, especially after the #47 change and the setup-node bump.)

## Subagent usage rules

- executor: workflow authoring, gcloud/vercel drift-check logic, issue migration, ADR/docs, the #47 fix
- verifier: tests/lint/types/build
- Each Part ships as its own PR where it makes sense (Part A standalone; B/C/D may combine if small)
- The staleness workflow's first live run is a workflow_dispatch — confirm it reports "no drift" against the current (current) production state

## Escalation rules (orchestrator MUST ask before doing)

- Ask if the frontend drift check needs a `VERCEL_TOKEN` secret — GG must create it (Vercel account settings → token → add as GitHub repo secret). Flag what GG needs to do.
- Ask if the backend drift check can't read the running revision's source commit cleanly (image SHA → commit mapping may need the github.sha image tag; confirm the deploy workflow tags images with the SHA and that it's recoverable).
- Ask before the #47 fix if moving the structlog config risks reordering startup or has side effects — when in doubt, document-and-close rather than risk startup behavior.
- Ask if backlog.md migration surfaces items that are actually substantial scope (a "backlog item" might be a real feature) — file as issue, don't try to build it.
- Ask if a single executor pass would touch more than 6 files.
- Never set ANTHROPIC_API_KEY. No `[skip ci]` (required check blocks it). Ask before new dependencies.

## Hard rules (DO NOT touch)

- `deploy-prod.yml` deploy/canary/promotion logic — correct as-is; the staleness check is a SEPARATE workflow, it does not modify the deploy workflow
- The GitHub environment protection rule on production — don't touch
- Backend application logic (except the contained #47 startup-log config if safe)
- Frontend source — no changes
- `apps/api/config/llm_routing.yaml`, prompts, thresholds, streaming event types, refine.py, search.py
- All existing ADRs (0001-0024) — create 0025
- Production secrets — read-only; GG creates any new secret (e.g., VERCEL_TOKEN)
- The staleness check MUST be alert-only — it must never call a deploy, tag a release, or trigger workflow_dispatch on the deploy workflow

## Budget

- Soft target: 1 Max plan window for all four Parts
- Hard cap: escalate if executor invocations exceed 20
- Cost check: /cost after Part A (the guardrail) lands

## Success criteria (orchestrator verifies ALL before declaring done)

**Part A — staleness guardrail:**
- [ ] `production-staleness-check.yml` created: scheduled cron + workflow_dispatch
- [ ] Backend drift check works (running prod revision commit vs main HEAD)
- [ ] Frontend drift check works (Vercel prod deployment commit vs main HEAD)
- [ ] Alert path verified: opens/updates a single stable-titled issue on drift; does NOT spam
- [ ] Confirmed it does NOT trigger any deploy
- [ ] First live workflow_dispatch run reports "no drift" against current (current) production
- [ ] Detection logic verified against a known-stale comparison (proves the alert fires when drift exists)
- [ ] VERCEL_TOKEN secret created by GG if needed (or alternative auth confirmed)
- [ ] ADR-0025 written (design + alert-not-deploy rationale)

**Part B — setup-node v5:**
- [ ] All setup-node@v4 → @v5
- [ ] Affected workflows pass (Web CI green on v5)

**Part C — backlog.md migration:**
- [ ] All backlog.md items migrated to GitHub issues or mapped to existing ones
- [ ] backlog.md deleted, references updated

**Part D — #47 startup-log:**
- [ ] Either fixed (startup logs use JSONRenderer) or documented-and-closed as benign
- [ ] If fixed: backend tests pass, no startup reordering side effects

**All Parts:**
- [ ] Coverage ≥ 80%, ruff + mypy clean, frontend builds
- [ ] No production deploy triggered
- [ ] No ANTHROPIC_API_KEY, no [skip ci]
- [ ] No HARD RULE file modified
- [ ] CURRENT_STATE.md updated; #45, #41, #42, #47 closed (or #47 documented-closed)

## Build order

1. Part A (staleness guardrail) first — the anchor, highest value, most design. Build, test against current state (expect no-drift), verify detection fires on known-stale input. PAUSE if VERCEL_TOKEN needed from GG.
2. Part B (setup-node) — trivial version bump, verify CI.
3. Part C (backlog migration) — catalog + file issues + delete.
4. Part D (#47) — fix if cheap, else document-close.
Each as its own PR (B/C/D may combine if small). /cost after Part A.

## Notes for the orchestrator

- Max plan covers Opus 4.7 + Sonnet — never set ANTHROPIC_API_KEY.
- The guardrail's entire purpose is preventing recurrence of the iteration-3/4 freeze. It must ALERT, never auto-deploy — production deploys are deliberately gated (canary + env approval) and the guardrail must respect that. Its job is to make sure nobody forgets the manual deploy step, not to remove it.
- Both production surfaces froze identically (auto-deploy not firing, two weeks unnoticed). The guardrail covers BOTH backend (Cloud Run) and frontend (Vercel).
- Backend drift detection: the deploy workflow tags images with `${{ github.sha }}` — that's the cleanest commit→deployment mapping. Confirm it's recoverable from the running revision.
- Frontend drift detection: Vercel CLI is authed locally as gaurav-gandhi-2411, but CI needs its own auth — likely a VERCEL_TOKEN secret. The Vercel REST API can return a deployment's `meta.githubCommitSha`.
- Use a stable issue title for alerts ("⚠️ Production staleness detected") and update-in-place so repeated cron runs don't spam.
- Read each issue (#45, #41, #42, #47) via `gh issue view` for full context before starting.
