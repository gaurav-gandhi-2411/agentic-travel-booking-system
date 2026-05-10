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

- **ADR (TBD): Frontend auth — Clerk**
  Document rationale for Clerk over NextAuth.js and Supabase Auth (multi-tenant org model,
  generous free tier, native Next.js integration). ADR number assigned in Phase 8 (after
  ADR-0012 from the open-source track). _(Decision made Phase 0.)_

- **ADR (TBD): Vercel AI SDK for streaming**
  Document rationale for Anthropic Python SDK on backend + Vercel AI SDK on Next.js
  frontend for streaming only. ADR number assigned in Phase 8. _(Decision made Phase 0.)_

- **KMS migration when commercial**
  Current encryption uses AES-256-GCM at the application layer (ADR-0007). When the
  project becomes commercial and Secret Manager costs are no longer the binding constraint,
  migrate tenant-credential encryption to Cloud KMS for HSM-backed key management and
  automatic rotation without application-layer re-encryption. _(Flagged ADR-0007.)_

- **vLLM serving when scale demands**
  Current inference uses Ollama (local) or OpenRouter/Groq (free cloud). When request
  volume justifies dedicated GPU infrastructure, replace with vLLM self-hosted serving
  (ADR-0008, VLLMAdapter stub in Phase 2.5). Trigger: sustained >500 req/day or SLA
  requirements that free-tier rate limits cannot meet. _(Flagged ADR-0008.)_

- **Cost ledger end-user visibility flag**
  Implement `show_cost_to_users: true` tenant config flag. Tenant admin dashboard shows
  cost by default; end-user chat UI hides it unless the flag is set. _(Decision made
  Phase 0; §15.)_

---

## Research Track

- **benchmark-protocol.md: enforce full-sample requirement in CI**
  `docs/research/benchmark-protocol.md` states that external reproducers must run the
  complete 20% public eval sample. Add a CI check (Phase 3.5 or 6.7) that asserts
  the benchmark run consumed all examples in `evals/datasets/` — not a subset. This
  prevents accidental subset runs from being reported as reproductions. _(Flagged sub-stage A.)_

- **Technical report §7 Limitations: 80% dataset proprietary**
  When writing the technical report (Phase 6.7), §7 Limitations must explicitly state
  that 80% of the golden eval dataset is proprietary and not publicly released. External
  reproducers work from the 20% public sample only; their results may differ from the
  reported numbers due to sample variation. This is documented in `paper-outline.md` §7
  but must carry through verbatim to the final submitted draft. _(Flagged sub-stage A.)_

## Phase 3

- **Eval strategy document**
  Write `docs/architecture/eval-strategy.md` covering: Claude-generated golden cases,
  human-review workflow, how cases are updated when prompts change. _(Flagged Phase 0.)_

---

## Resolved / In-scope

- ~~**Neon cold-start mitigation**~~ → Handled in Stage 0.4 cloud-setup runbook via Cloud
  Scheduler cron hitting `/health` every 4 minutes. _(Risk 1.)_
