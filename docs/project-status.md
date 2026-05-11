# Project Status

Last updated: 2026-05-11

## Phase / Stage state
- Last completed: Phase 0 — Stage 0.4 (staging deploy chain verified end-to-end; all runbook sections closed except deferred §10)
- In progress: Phase 0.5 marketing site deployed; affiliate application approvals pending (external, gates hotel providers in Phase 1)
- Next: Phase 1 authorization — explicit owner go required (plan.md §11)

## Cloud provisioning
- Runbook sections complete: §1 GCP project, §2 SA, §3 WIF, §4 Secret Manager seed, §5 AES-256 key, §6 Cloud Run stubs, §7 Cloud Scheduler, §8 Neon Postgres, §9 Upstash Redis, §11 Anthropic (no action by design), §12 Travelpayouts, §13 GitHub Actions secrets/vars, §14 deploy workflows enabled, §15 smoke test
- Runbook sections deferred: §10 Vercel env vars — `NEXT_PUBLIC_API_BASE_URL` not set; Vercel project linked and marketing site live, but frontend does not talk to backend until Phase 8; deferred to Phase 8
- All credentials in Secret Manager: Yes — all 13 secrets present. Phase 0 secrets (Neon, Upstash, Travelpayouts, jwt-signing-key, tenant-credential-master-key) hold real values. Clerk and Sentry secrets retain placeholder values by design; provisioned in later phases.

## Operational state
- Vercel: Project linked (`apps/web` root), marketing site deployed on Hobby plan. `NEXT_PUBLIC_API_BASE_URL` not set (deferred to Phase 8). Clerk keys are placeholder values.
- Affiliate applications: Status not confirmed in these sessions — applications to Booking.com, Agoda, Hotellook, Trip.com should have been submitted immediately after Phase 0.5 marketing site deploy (plan.md §11 Phase 0.5). Confirm submission dates and track approval timelines; approvals gate hotel provider adapters in Phase 1.
- Outstanding human action items:
  - Confirm affiliate applications submitted to all four programs and update this file with submission dates and expected approval windows
  - Track affiliate approval status (gates Phase 1 hotel providers)
  - Authorize Phase 1 when ready (explicit go required — not yet given as of this file)

## Recent decisions not yet reflected in plan.md
- Runbook §10 (Vercel API base URL env vars) deferred to Phase 8 — 2026-05-11
- Phase 0 baseline scaffolding (minimum FastAPI stub + Dockerfile) landed in Phase 0 rather than Phase 1 to enable staging deploy workflow verification; documented in plan.md §11 Phase 0 bullet and Phase 1 opening note

## How to resume work in a new session
- Read this file
- Read plan.md §11 for phase context
- Read scratch/provisioning-ledger.md (gitignored, your laptop only) for resource IDs
- Read docs/backlog.md for deferred items
- Last commit on main: 706edef — docs: project-status.md for cross-session resumption (note: hash is pre-final-amend; run `git log -1` for the authoritative tip)
