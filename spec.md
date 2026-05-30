# spec.md — Phase 2D iteration 4: Production frontend audit + alignment

## Session goal

Iteration 3 brought the production BACKEND to current main (v0.6.0, commit 3d30839)
after finding it frozen at v0.5.0 for two weeks. The production FRONTEND on Vercel
points at that backend and has NOT been audited. Given the backend was three phases
stale, the frontend may be too. This iteration audits it and aligns it if stale.

## Why this iteration exists

Iteration 3 found the backend silently frozen because staging auto-deploys but
production needed a manual trigger nobody pulled. The frontend may have the same
asymmetry. Critically: a STALE frontend won't throw errors — the current frontend
gracefully ignores unknown SSE events, so a stale one would just SILENTLY fail to
render the chat UI when the now-current backend sends `conversation_thinking` /
`conversation_action_classified` / `conversation_message` events. Absence of console
errors does NOT prove currency. The positive check (chat components present, bubbles
actually render) is what proves it.

## Key facts

- Production backend (current): `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`
  — this is the canonical URL the frontend's `API_BASE_URL` env var should target.
- Chat UI shipped in PR #32; 4-profile selector in PR #22. If the prod Vercel deploy
  predates these, it's missing the conversational UI.
- Frontend is `apps/web` (Next.js 15, React 19), deployed on Vercel.
- Vercel project is linked (`.vercel/project.json` present,
  `projectId: prj_t4WA8OGPAIAxZIuAidmd6Rm4AZPX`, `projectName: agentic-travel-booking-system`).
- No `vercel.json` — Vercel uses auto-detection for the Next.js app in `apps/web/`.
- No Vercel deploy step in any `.github/workflows/` — Vercel's GitHub integration
  is the expected auto-deploy mechanism (deploys on every push to main).
- The orchestrator lacks Vercel CLI auth — Vercel dashboard and env changes are GG
  actions. Confirm in Phase 0.
- Browser visual verification is GG (subagents can't drive a browser).
- Orchestrator does the bundle/network-level check: fetch prod URL, inspect served
  JS for chat component strings (`ChatLog`, `conversation_thinking`, etc.).

## Gated structure

```
Phase 0 — discover (read-only)                          PAUSE after, report
  0.1  GG provides Vercel production URL (or orchestrator infers it)
  0.2  Orchestrator: fetch prod URL — inspect served HTML/JS for:
         ChatLog, ChatMessage, ProfileToggle, conversation_thinking,
         conversation_action_classified, args_summary
       Presence of these strings = frontend is current (post-PR#32)
       Absence = stale
  0.3  GG checks Vercel dashboard:
         - Last deployment date + commit SHA
         - API_BASE_URL env var value (prod env) — staging URL or prod URL?
         - Deploy trigger: auto on push to main, or manual?
  0.4  Orchestrator: report staleness finding, API_BASE_URL target, deploy
       mechanism, and whether Vercel auto-promotes main→production with no
       preview gate (flag if so — GG approves knowingly)

Phase 1 — align via Vercel deploy IF stale (GG-approved)
  1.1  IF frontend code is stale: GG triggers Vercel redeploy from current main
  1.2  IF API_BASE_URL points to staging: GG updates Vercel env var to prod
       Cloud Run URL, then redeploys
  1.3  Orchestrator: re-fetch prod URL after deploy, re-check JS strings,
       confirm chat components now present

Phase 2 — end-to-end verification
  2.1  Orchestrator: fetch prod URL, confirm chat component strings in JS bundle
  2.2  GG: browser visual check — load prod URL, run a search, confirm:
         - 4-profile selector renders (ProfileToggle)
         - AgentProgressFeed rows appear during streaming
         - ChatLog chat bubbles render after a refinement
         - No console errors on conversation_* SSE events
  2.3  Orchestrator: GET /health on production backend — confirm still {"status":"ok"}

Phase 3 — docs + close-out
  3.1  Update CURRENT_STATE.md: production frontend state, Vercel deploy mechanism,
       API_BASE_URL value, last-deploy commit SHA
  3.2  Commit + push CURRENT_STATE.md update
  3.3  (Optional) ADR-0024 if a non-obvious decision was made (e.g., API_BASE_URL
       override pointing to wrong env)
```

## Gates

- **After Phase 0:** report frontend staleness + backend-target config + deploy
  mechanism. STOP. GG + external engineer review before any Vercel action.
- **Before any Vercel production action:** GG approves (and likely triggers, due to
  Vercel auth).
- **If Vercel auto-promotes main→production with no preview gate:** flag so GG
  approves knowingly (every push to main would immediately reach production).

## What the orchestrator does vs. what GG does

| Step | Owner |
|------|-------|
| Fetch prod URL, inspect bundle JS for component strings | Orchestrator |
| Vercel dashboard: last deploy date, commit SHA, env vars | GG |
| Vercel dashboard: API_BASE_URL value in production env | GG |
| Trigger Vercel redeploy (if stale) | GG |
| Update API_BASE_URL env var in Vercel (if wrong) | GG |
| Browser visual: 4-profile selector + chat bubbles render | GG |
| GET /health on production backend | Orchestrator |
| CURRENT_STATE.md update + commit | Orchestrator |

## Scope

### In scope

- Read-only frontend audit (fetch prod URL, inspect JS)
- Determining Vercel deploy mechanism and env var values
- If stale: coordinating GG to trigger Vercel redeploy
- If API_BASE_URL is wrong: flagging it for GG to fix
- End-to-end verification (bundle check + GG browser visual)
- CURRENT_STATE.md update

### Out of scope

- Frontend source code changes (unless a build-blocking bug is found — escalate first)
- Backend changes (backend is current — do not touch)
- New features, refactoring, or any code changes not required for alignment
- Phase 3 features (BookingAgent, hotel data, etc.)

## Tech stack

- Vercel (Next.js auto-detected from `apps/web/`)
- GitHub integration (auto-deploy on push to main — to be confirmed)
- Cloud Run production backend (read-only reference)

## Success criteria (orchestrator verifies ALL before declaring done)

**Phase 0:**
- [ ] Vercel production URL confirmed
- [ ] JS bundle inspected — staleness verdict: current or stale
- [ ] API_BASE_URL value in Vercel production env documented
- [ ] Deploy mechanism documented (auto vs. manual, any preview gate)
- [ ] Reported and reviewed before any Vercel action

**Phase 1 (if stale):**
- [ ] GG approval obtained
- [ ] Vercel redeploy triggered (by GG)
- [ ] API_BASE_URL corrected (by GG) if pointing to staging
- [ ] Orchestrator confirmed deploy completed (JS bundle re-checked)

**Phase 2:**
- [ ] JS bundle confirms chat components present (ChatLog, conversation_* strings)
- [ ] GG confirms browser visual: 4-profile selector + chat bubbles render
- [ ] Production backend /health still {"status":"ok"}

**Phase 3:**
- [ ] CURRENT_STATE.md updated with frontend production state
- [ ] Commit + push completed
- [ ] (If applicable) ADR-0024 written

**All phases:**
- [ ] No frontend source code changed (audit-only, unless escalated)
- [ ] No backend code changed
- [ ] No ANTHROPIC_API_KEY set
- [ ] No [skip ci] used
- [ ] Each phase gate respected

## Conventions

- Never set `ANTHROPIC_API_KEY`
- No `[skip ci]` in commits (required check blocks it)
- No backend changes (backend is current — don't touch)
- No frontend source edits unless a build-blocking bug is found (escalate first)
- Production secrets read-only for subagents

## Notes for the orchestrator

- The chat UI presence check is the POSITIVE proof of currency — absence of console
  errors on the current frontend is NOT sufficient (it silently ignores unknown events).
- The `API_BASE_URL` risk is subtle: even if the frontend code is current, pointing
  at staging means the prod frontend calls the staging backend (different data, shared
  load). This is a misconfiguration that won't throw errors.
- If Vercel auto-deploys on every push to main (no preview gate), the frontend is
  almost certainly current — but the API_BASE_URL env var still needs verification.
- Local `.env.local` shows `API_BASE_URL=https://agentic-travel-booking-api-staging-rqyyasfwaa-el.a.run.app`
  — this is local dev config and does NOT reflect what Vercel has set in its prod env.
  GG must check the Vercel dashboard directly.
