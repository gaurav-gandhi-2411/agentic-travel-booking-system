# Backlog

Items deferred from current phase scope. Each entry notes the originating phase and reason.

---

## Phase 11

- **Tenant provider account onboarding guide**
  Amadeus and Duffel require per-tenant developer accounts (we never share a credential
  pool — §8.4). Add a step-by-step walkthrough to `docs/runbooks/tenant-onboarding.md`
  and frame in `docs/customer/integration-guide.md` as "you keep your existing provider
  relationships and quotas." _(Flagged Phase 0; Risk 2.)_

- **Agent eval: 50+ golden cases per agent**
  Phase 3 sign-off bar is 10–20 cases/agent. Expanding to 50+ is a stretch target moved
  here. Cases are Claude-generated then human-reviewed; workflow documented in
  `docs/architecture/eval-strategy.md` (written Phase 3). _(Flagged Phase 0; Risk 4.)_

---

## Phase 8

- **ADR-0007: Frontend auth — Clerk**
  Document rationale for Clerk over NextAuth.js and Supabase Auth (multi-tenant org model,
  generous free tier, native Next.js integration). _(Decision made Phase 0.)_

- **ADR-0008: Vercel AI SDK for streaming**
  Document rationale for Anthropic Python SDK on backend + Vercel AI SDK on Next.js
  frontend for streaming only. _(Decision made Phase 0.)_

- **Cost ledger end-user visibility flag**
  Implement `show_cost_to_users: true` tenant config flag. Tenant admin dashboard shows
  cost by default; end-user chat UI hides it unless the flag is set. _(Decision made
  Phase 0; §15.)_

---

## Phase 3

- **Eval strategy document**
  Write `docs/architecture/eval-strategy.md` covering: Claude-generated golden cases,
  human-review workflow, how cases are updated when prompts change. _(Flagged Phase 0.)_

---

## Resolved / In-scope

- ~~**Neon cold-start mitigation**~~ → Handled in Stage 0.4 cloud-setup runbook via Cloud
  Scheduler cron hitting `/health` every 4 minutes. _(Risk 1.)_
