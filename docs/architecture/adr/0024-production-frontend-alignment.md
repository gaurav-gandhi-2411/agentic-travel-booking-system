# ADR-0024 — Production Frontend Alignment: Vercel Freeze + Empty Env Vars (Phase 2D Iteration 4)

## Context

Phase 2D iteration 4 audited the production Vercel frontend after iteration 3 found the
backend had been silently frozen at v0.5.0 for two weeks. The hypothesis was that the
same asymmetry (staging auto-deploys, production does not) might affect the frontend.
The audit confirmed it did — and found a second, independent failure on top of the
stale code.

### Finding 1 — Stale code (same freeze window as backend)

The Vercel production deployment was frozen at commit `034bc03`
("chore: refresh contributor stats", 2026-05-16), predating:

| PR | Missing feature |
|----|----------------|
| #22 (`eea11d3`) | 4-profile selector: added `demo-gpt-oss-120b`, `demo-deepseek-v4`; removed `demo-qwen` |
| #32 (`02f3345`) | Chat-style refinement UI: `ChatMessage.tsx`, `ChatLog.tsx`, all `conversation_*` SSE handling |

Confirmed via JS bundle inspection: `demo-qwen` was present in the localStorage guard;
`conversation_thinking`, `conversation_action_classified`, `args_summary`, `ChatLog`,
`ChatMessage` were all absent. The stale frontend would silently discard every SSE
event the now-current backend sends for the conversational refinement path — no console
errors, no visible failure, just a missing chat UI.

### Finding 2 — Empty env vars (production never functioned for searches)

Both `API_BASE_URL` and `DEMO_API_KEY` had been set to **empty strings** in Vercel's
production environment since May 15 (the day before the last deployment). The Next.js
API routes use:

```ts
const apiBase = process.env.API_BASE_URL ?? 'http://localhost:8000';
```

The `??` operator catches only `null` and `undefined` — not empty string. With
`API_BASE_URL = ""`, `apiBase = ""`, and every call became `fetch("" + "/search")`.
In a Node.js serverless context this throws a URL parse error, surfacing to the client
as `data: {"type":"error","message":"fetch failed"}`.

Combined with the stale code, this means the production frontend had **never functioned
for searches** since initial deployment on May 15. Any demonstrations used local dev or
the staging environment.

### Finding 3 — Vercel GitHub integration not auto-deploying

The Vercel project is linked to the GitHub repo (`.vercel/project.json` present,
`projectId: prj_t4WA8OGPAIAxZIuAidmd6Rm4AZPX`). Despite this, pushes to `main` did
not trigger production deployments after May 16. The 14 prior production deployments
all occurred during the initial project setup burst (May 14-16). The root cause —
whether the production branch setting, the auto-deploy toggle, or the GitHub app
integration state — is dashboard-only and was not confirmed during this iteration.

### Finding 4 — Vercel CLI v54.0.0 preview env var bug

`vercel env add NAME preview --value X --yes` returns `git_branch_required` even with
the `--value` and `--yes` flags the CLI itself suggests as the non-interactive form.
Preview-scoped env vars for "all preview branches" cannot be set non-interactively via
the CLI in this version. This made preview smoke-testing harder than expected: the two
preview deployments created during this iteration could not receive the correct
`API_BASE_URL` without dashboard access, and production-scoped vars do not propagate
to preview.

Additionally, Vercel env vars are baked into serverless function bundles at deploy
time — not read dynamically at request time — even for routes marked `force-dynamic`.
Changing an env var in the dashboard does not affect existing deployments; a redeploy
is required.

## Decision

Fix the production frontend in two steps, skipping the preview smoke-test of backend
connectivity (which was blocked by the preview env var tooling limitation):

1. **Fix env vars first:** Remove the empty-string values for `API_BASE_URL` and
   `DEMO_API_KEY` from the Production scope and add correct values via
   `vercel env rm` / `vercel env add`.

2. **Deploy current `main` to production:** `vercel deploy --prod --archive=tgz` from
   the repo root. The `--archive=tgz` flag is required to stay under Vercel's
   15,000-file upload limit (local `node_modules` are present on disk). The Vercel
   project "Root Directory" is set to `apps/web/` in the dashboard — the CLI must be
   run from the repo root, not from `apps/web/` (running from `apps/web/` doubles the
   configured path and fails).

The decision to skip the preview gate for backend connectivity was based on: (a) the
code correctness was already confirmed via bundle inspection of both preview
deployments; (b) the preview env var tooling limitation made the preview backend test
meaningless — the preview would never get the right `API_BASE_URL` without a dashboard
action that GG would need to take anyway; (c) the production env vars were already
confirmed correct via CLI; (d) production was already broken (empty env vars), so the
downside floor was the status quo.

## Consequences

**Positive:**
- Production frontend is current with `main` HEAD for the first time since initial
  setup. PRs #22 (4-profile selector) and #32 (chat UI) are now live.
- `API_BASE_URL` and `DEMO_API_KEY` correctly set for Production scope. Every
  `fetch` in the Next.js API routes now resolves to the production Cloud Run URL.
- Both production surfaces (Cloud Run backend + Vercel frontend) are current and
  end-to-end verified for the first time. The production demo is functional.
- End-to-end smoke test passed: `/api/search` → full SSE pipeline reaching Cloud Run
  production; `/api/refine` → `conversation_thinking` → `conversation_action_classified`
  (action=refine, LLM-generated `args_summary`) → Redis cache hit → archetypes.
  Confirmed against Cloud Run logs via `request_id`.
- GG browser visual passed: 4-profile selector, progress feed, archetype cards,
  user/thinking/action/message chat bubbles, NO_OP `conversation_message`, zero
  console errors.

**Negative / observations:**
- The Vercel GitHub integration's non-auto-deploy behavior is unconfirmed at the root
  cause level (dashboard investigation needed). Future production frontend deploys
  require a manual `vercel deploy --prod --archive=tgz` trigger. Folded into Issue #45
  scope (staleness guardrail must now cover both surfaces).
- Preview env var scoping via CLI (v54.0.0) is broken for "all preview branches."
  Until fixed upstream or Vercel CLI is upgraded, preview-scoped env vars must be set
  via the Vercel dashboard.
- The freeze detection gap that allowed two weeks of non-functional production (both
  backend and frontend) was not addressed in this iteration. Issue #45 captures this.

## Alternatives

**Alternative 1 — Fix env vars only, do not redeploy code.** Rejected: even with
correct env vars, the deployed code (034bc03) is missing the chat UI (PR #32) and the
4-profile selector (PR #22). Searches would succeed but the UI would be functionally
wrong for the conversational demo path.

**Alternative 2 — Redeploy via Vercel dashboard (GG manual).** Viable, but the
orchestrator had Vercel CLI auth and could execute it directly. CLI was the faster
path and kept the operation in the session's audit trail.

**Alternative 3 — Force Vercel GitHub auto-deploy by re-triggering the integration.**
Not attempted: the root cause of the non-auto-deploy is unknown. Re-triggering without
understanding the root cause could produce unexpected results (e.g., if the production
branch is misconfigured, re-enabling auto-deploy could start promoting every push to
`main` to production with no review gate). Deferred to Issue #45.

## Rollback

```bash
# Re-promote the prior production deployment (broken, but available)
vercel rollback https://agentic-travel-booking-system-1moje3icn.vercel.app
# Or via CLI promote
vercel promote agentic-travel-booking-system-1moje3icn.vercel.app --scope=gaurav-gandhi-2411s-projects
```

The prior production deployment (`dpl_4diNXVLG99uqBaA1YE5sJFQVsmn8`,
`agentic-travel-booking-system-1moje3icn.vercel.app`) remains in Vercel's deployment
history in Ready state. Note: rolling back would restore the stale code AND the
empty-string env vars — searches would immediately fail again. Rollback is a last
resort; it does not restore a working state.
