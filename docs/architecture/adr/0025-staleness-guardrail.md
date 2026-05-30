# ADR-0025 — Production Staleness Guardrail

**Date:** 2026-05-31  
**Status:** Accepted  
**Phase:** 2D Iteration 5

---

## Context

Iterations 3 and 4 each revealed that a production surface had silently frozen:

- **Iteration 3 (2026-05-30):** Cloud Run backend was running v0.5.0 (commit `78c57db`, tagged 2026-05-15), two phases of code behind main. Staging auto-deploys on every push; production requires manual trigger. The gap accumulated unnoticed for ~two weeks.
- **Iteration 4 (2026-05-31):** Vercel frontend was frozen at commit `034bc03` ("chore: refresh contributor stats", 2026-05-16) — the same two-week window. Both `API_BASE_URL` and `DEMO_API_KEY` were empty strings in the Production env scope, meaning every search request failed silently since initial setup.

Both surfaces share the same root cause: **staging deploys automatically, production does not, and there is no mechanism to detect or surface the growing gap.** The divergence was only discovered during manual production audits triggered by unrelated work.

The iteration-3/4 remediation (deploying the backend, aligning the frontend, verifying end-to-end) took significant work. A guardrail that had alerted after the first missed deploy would have prevented both remediations entirely.

---

## Decision

Build a **scheduled GitHub Actions workflow** (`production-staleness-check.yml`) that detects when either production surface drifts behind `main` and opens a GitHub issue. The workflow is alert-only — it **never triggers a deploy**.

### Why alert-only, never auto-deploy

Production deploys are deliberately gated:
1. A manual canary stage gate (`workflow_dispatch stage=canary`) deploys the revision at 0% traffic
2. GG performs a manual smoke test (correctness, cache hits, ConversationManager path)
3. A `workflow_dispatch stage=full` (gated by the `production` GitHub environment — requires GG approval) shifts traffic and soaks the canary

This two-gate structure exists precisely because "the code merged" is not sufficient justification to flip production traffic. The guardrail's job is to make sure nobody **forgets** the manual deploy step — not to remove it. Any design that auto-deploys on staleness detection undermines the canary discipline and was explicitly rejected.

---

## Architecture

### Trigger

- `schedule: cron '0 4 * * *'` — daily at 04:00 UTC (offset from eval-optimizer nightly at 03:00)
- `workflow_dispatch` — on-demand with optional `test_stale: boolean` input for verifying the alert path

### Backend drift detection (Cloud Run)

1. `gcloud run services describe agentic-travel-booking-api-prod` → `status.latestReadyRevisionName`
2. `gcloud run revisions describe <revision>` → `status.imageDigest` (sha256)
3. `gcloud artifacts docker images list ... --filter="DIGEST='<digest>'"` → scan comma-separated tags for a 40-character hex string — the deploy workflow always pushes both `api:$ref_name` and `api:$github.sha`, so the SHA tag is always present
4. `git rev-list <deployed-sha>..HEAD -- apps/api/ | wc -l` → commits behind, scoped to backend files only

Auth: reuses the existing WIF service account (`travel-agent-deployer`) with read-only gcloud. No new IAM permissions needed.

### Frontend drift detection (Vercel)

1. `GET https://api.vercel.com/v6/deployments?teamId=...&projectId=...&target=production&state=READY&limit=1` with `Authorization: Bearer $VERCEL_TOKEN`
2. Extract `meta.githubCommitSha` (or `gitSource.sha` as fallback) from the response
3. `git rev-list <deployed-sha>..HEAD -- apps/web/ | wc -l` → commits behind, scoped to frontend files only

Auth: requires a `VERCEL_TOKEN` GitHub secret (GG creates: Vercel account settings → Tokens). The Vercel CLI is authenticated locally but CI needs its own bearer token.

### Path scoping rationale

Backend and frontend drift checks are scoped to their respective app directories (`apps/api/` and `apps/web/`). This prevents false alerts when a commit touches only the other surface's files (e.g., a docs commit or a backend-only change should not trigger a frontend staleness alert). The comparison is: "does main have commits that touch this surface's code that production hasn't received?"

### Alert mechanics

- **Label:** `production-staleness-alert` (created by the workflow if absent)
- **Title:** `⚠️ Production staleness detected` (stable — used to find and update the existing issue)
- **Open/update:** If either surface is behind, `gh issue list --label production-staleness-alert --state open` finds the existing alert issue; `gh issue edit` updates the body with current drift details. If no open alert issue exists, `gh issue create` opens one.
- **Auto-close:** If both surfaces are current, any open alert issue is closed with a resolution comment.
- **No spam:** Repeated cron runs update one issue rather than opening duplicates.

### Test mode

`workflow_dispatch` with `test_stale: true` substitutes known-stale SHAs (v0.5.0 backend at `78c57db`, frozen frontend at `034bc03`) for the real production queries. This allows verifying the alert path fires without leaving production stale. The opened issue is clearly marked as a test run.

---

## Consequences

**Positive:**
- If either surface drifts, a GitHub issue surfaces within 24 hours of the first missed deploy
- The issue body includes exact SHA comparisons, commits-behind counts, diff links, and deploy instructions — reducing the time to diagnose and act
- The auto-close mechanic keeps the issue list clean: the alert is present when needed, gone when resolved
- Both surfaces use different auth paths (WIF/gcloud for backend, VERCEL_TOKEN for frontend), matching how their respective deploys work

**Negative / tradeoffs:**
- Requires a `VERCEL_TOKEN` GitHub secret that GG must create and rotate. Token loss disables the frontend check (with a `::warning::` annotation but no hard failure).
- The backend SHA lookup chain (revision → digest → Artifact Registry tags) has three gcloud calls. If the 40-char SHA tag is absent (e.g., a deploy that skipped the SHA push step), the check emits a warning and marks backend drift as unknown (-1), which triggers the alert conservatively.
- `latestReadyRevisionName` is used as a proxy for "100%-traffic revision." This is correct after a successful `--to-latest` promotion but could return a newer failed revision if a deploy was interrupted after the canary step. The impact is a false-positive staleness alert, not a false-negative — conservative in the right direction.

---

## Alternatives considered

**Option 1 — GitHub Actions API to find last successful deploy run.** Using `GET /repos/{owner}/{repo}/actions/workflows/deploy-prod.yml/runs?status=success&per_page=1` to get `head_sha`. Rejected because the 2-stage deploy (canary + full are separate `workflow_dispatch` triggers) means `head_sha` of the most recent "successful" run is unreliable — a `stage=full` run doesn't rebuild the image, so its `head_sha` is the commit that triggered it, not necessarily what's running.

**Option 2 — Git tag as ground truth for backend.** Using `git rev-list -n 1 v0.6.0` to resolve the deployed tag to a commit. Works when deployed via `push: tags: v*`, but not when deployed via `workflow_dispatch` (where `github.ref_name` = "main", making the image tag non-unique). The Artifact Registry SHA tag approach works for both trigger types.

**Option 3 — Scheduled reminder comment on PRs / in deploy-staging.yml logs.** Zero-infra, but passive — only visible to someone actively watching logs or browsing PRs. Would not have caught the two-week silences. Rejected as insufficient.

**Option 4 — PR template checklist.** "Does this PR need a prod deploy?" checkbox. Also passive and relies on author discipline. Same objection.

---

## References

- Issue #45 — Production silently froze (original filing)
- ADR-0023 — Production deploy v0.6.0 (backend staleness remediation)
- ADR-0024 — Production frontend alignment (frontend staleness remediation)
- `.github/workflows/production-staleness-check.yml` — implementation
- `.github/workflows/deploy-prod.yml` — the gated deploy workflow this guardrail watches (but does not modify)
