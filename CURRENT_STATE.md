# CURRENT_STATE.md — agentic-travel-booking-system

Context that isn't in the code. The orchestrator can read the repo for *what*; this doc explains *why*.

## Project goal

An agentic flight-search system that runs on free-tier LLM infrastructure to demonstrate cost-effective AI for B2B clients. Production target: 99% gross margin on the open-source profile, with paid Anthropic available as an opt-in premium tier. Coordinator pattern (deterministic Python orchestration), not autonomous multi-agent — Planner → FlightHunter → Optimizer pipeline with SSE streaming, plus a recently-added ConversationManagerAgent for natural-language refinement.

## Where to look first

Don't trust this document for repo structure — read these directly:

```bash
# Run these as your first orientation
git log --oneline -30
ls apps/api/src/travel_agent/
cat AUDIT_REPORT.md                    # original audit at project root
ls docs/architecture/adr/              # all ADRs, read 0019 most recently added
```

The repo has been actively developed across multiple phases. `git log` will show the recent arc better than I can describe it.

## Load-bearing files (don't casually touch)

These have hard-won design decisions baked in. Each has an ADR or a Phase document explaining why.

**`config/llm_routing.yaml`** — Defines the 4 active demo profiles (Haiku, Llama 3.3, DeepSeek V4 Flash, GPT-OSS-120B) plus the eval-judge profile. Profile-name changes cascade across the codebase. Adding a new profile requires the orchestration steps documented in ADR-0008 and ADR-0019.

**`apps/api/src/travel_agent/llm/__init__.py`** and the adapter files (`anthropic.py`, `groq.py`, `nvidia.py`) — The provider-agnostic LLM client interface. Critically, NIM uses an OpenAI-compatible transport (no separate SDK), Groq case-sensitivity quirks are handled at both schema and validator layers, and the `extra_params` plumbing is required for `reasoning_effort` on GPT-OSS-120B.

**`apps/api/src/travel_agent/evals/optimizer/thresholds.py`** — Gate values derived from canonical baseline runs. Per-provider completion thresholds (NVIDIA differs from Groq for credit-pool reasons). Changing these changes what "regression" means.

**`apps/api/src/travel_agent/agents/optimizer.py`** — The system prompt has an explicit constraint against citing departure times (Haiku was hallucinating them, Issue #14). Don't regenerate or "improve" this prompt without re-baselining all profiles against it.

**`apps/api/src/travel_agent/agents/conversation_manager.py`** + `conversation_manager_types.py` + `prompts/conversation_manager_system.txt` — Brand new (PR #25, PR #27). The args_summary field is LLM-generated and the prompt instructs natural-language phrasing — touch carefully. Locked at Level 2 ambition (single-turn understanding, no persistent memory beyond SearchCache TTL).

**`apps/api/src/travel_agent/evals/optimizer/runner.py`** — The `_PROFILES` default excludes Haiku (opt-in for cost discipline) and excludes NIM-hosted profiles (credit pool incompatible with nightly cadence). This is intentional. ThrottledLLMClient handles Groq TPM but NOT credit pools.

**`apps/api/src/travel_agent/observability/pricing.py`** — Per-model rates including NIM models at $0 and Groq judge at $0. Cost surfacing is wired through scorer; "Anthropic spend" appears on every eval run output with `!!` prefix when > 0.

**`.github/workflows/deploy-staging.yml`** — Real deploy on push to main. Production deploy requires manual approval (GitHub Environments). Workload Identity Federation, no service account keys.

## Non-obvious conventions

**LLM profile naming.** Profiles are named `demo-<short-name>` (e.g., `demo-llama`, `demo-gpt-oss-120b`). The "demo-" prefix means "production-facing demo profile" — these appear in the frontend selector. Eval-only profiles use `eval-judge-<name>`. Don't drop the prefix.

**Cost discipline rule.** Paid Anthropic spend must be visible. Any new agent or eval that touches `demo-haiku` should surface its expected cost in the PR description. Default profile lists should never include Haiku without explicit reasoning in the commit message.

**Eval gate philosophy.** Thresholds are set tight on purpose. If Llama completion is 87.5% and threshold is 0.83, that's a deliberate "one more failure surfaces the underlying issue" gate, not a margin for safety. Don't relax thresholds to make failing evals pass — fix the underlying issue or document it.

**Pydantic discipline.** Every LLM tool-use response goes through Pydantic models with `model_validator` for cross-field invariants. The ConversationManagerOutput uses exactly-one-of args validation. When adding new agent types, mirror this pattern — don't trust LLM output without schema validation.

**SearchCache shape.** Redis-backed, 30-min TTL, keyed by request_id, holds `(TravelIntent, list[FlightOption], optional archetypes)`. No Postgres for user data (Neon is provisioned but unused, deferred to Phase 3). Don't introduce a new persistence layer without ADR.

**SSE event ordering matters.** The frontend's `useSearchStream` hook accumulates events to state. New event types automatically reach the UI via graceful fallthrough — but the *order* of events is the protocol. `conversation_thinking` must always fire before `conversation_action_classified`. Tests verify this.

**Groq schema gotcha.** GPT-OSS-120B on Groq rejects lowercase enum values at the schema pre-validation layer (before request reaches Python). Llama 3.3 doesn't have this issue. Fix is two-layer: schema enum includes uppercase variants AND Pydantic field_validator normalizes case on response. See Issue #29.

**`[skip ci]` in commit messages is a footgun.** When squash-merging, `[skip ci]` in any branch commit inherits into the squash commit and suppresses all main-branch workflows including Deploy-Staging. Pattern hit us during PR #27 merge. See Issue #30.

**`.env` loading via `find_dotenv()`.** Scripts that load API keys must use `dotenv.find_dotenv()` rather than relative paths. Relative paths break depending on CWD (different scripts run from different directories). Some legacy scripts use relative paths; tracked as Issue #49.

**localStorage cleanup pattern.** The frontend `ProfileToggle.tsx` defensively clears stale profile IDs from localStorage on load. When changing the active profile set, update both the union type AND the localStorage guard's allowlist.

**Vercel deploy mechanism (confirmed Phase 2D iteration 4).** The Vercel GitHub integration IS linked (`.vercel/project.json` present) but does NOT auto-deploy to production. Production branch is `main`, but pushes to `main` do not trigger production deploys — all 14 production deploys occurred during the initial project setup burst (May 14-16). Use `vercel deploy --prod --archive=tgz` from the repo root to trigger production deploys via CLI (authenticated as `gaurav-gandhi-2411`). The `--archive=tgz` flag is required to stay under Vercel's 15,000-file limit (node_modules are on disk). Vercel project "Root Directory" is set to `apps/web/` in the dashboard — do NOT run `vercel` from `apps/web/` (it doubles the path and fails).

**Vercel CLI v54.0.0 preview env var bug.** `vercel env add NAME preview --value X --yes` returns `git_branch_required` even with the flags the CLI itself suggests. Preview-scoped env vars cannot be set non-interactively via CLI. Set them in the Vercel dashboard under Settings → Environment Variables → Preview → All Preview Branches. Production-scoped vars do NOT propagate to preview. Vercel env vars are baked at deploy time even for `force-dynamic` routes — a redeploy is required after any env var change.

## Important decisions and the "why"

**Groq + NIM, not just one provider.** Groq has fast inference and daily-reset quotas; NIM has model variety and lifetime credit pool. Operationally different. We use Groq for nightly evals (resettable), NIM for opt-in demos (credit-pool aware). Phase 2C.2 substrate work documented this asymmetry in ADR-0008.

**Llama 3.3 as default for ConversationManagerAgent (not GPT-OSS-120B).** Phase 2C.4 PR 1 cross-profile eval showed Llama 100% action accuracy vs GPT-OSS 93.3%. GPT-OSS missed one borderline scenario (budget update — REFINE vs REPLAN). Llama is faster and matched accuracy on the classification task. See ADR-0019.

**GPT-OSS-120B at `reasoning_effort: low`.** Default `reasoning_effort: medium` produces ~500 hidden reasoning tokens, hitting max_tokens before tool response completes. Discovered during Phase 2C.3 baseline (4/24 truncation failures). The fix is in the `extra_params` on the profile. Don't remove this.

**Haiku-opt-in (not default).** Phase 2C.1 established empirical parity between Llama (free) and Haiku (paid) on coherence. Phase 2C.2 dropped Haiku from nightly default to enforce cost discipline. Re-introducing Haiku to nightly defaults requires explicit justification.

**Cache breakpoint placement (Phase 2C.4.5 prep).** Anthropic prompt caching is the next iteration's focus. The audit step is critical because Phase 2A noted "caching wired but no-op until prompts >1024 tokens." Don't assume existing caching is working — verify with the API's `cache_creation_input_tokens` and `cache_read_input_tokens` usage fields.

**OpenRouter as free-routing primary.** `config/llm_routing.yaml` uses OpenRouter as the primary provider for the `free` routing profile (not just experimental scaffolding). `OPENROUTER_API_KEY` is bound to the prod service for on-demand activation via `LLM_ROUTING_PROFILE=free` header override. Currently prod runs `LLM_ROUTING_PROFILE=demo` so OpenRouter isn't invoked in normal traffic. Groq is the fallback.

## Phase 3.2-A — Tenancy Foundation — COMPLETE (2026-06-08, local/test only)

Code-complete, locally tested. **Not deployed — prod still on `00025-gaw` with no Postgres.**

### What shipped

- `tenancy/` module: `Tenant` + `ApiKey` SQLAlchemy 2.0 models, `service.py` (key generation / SHA-256 hashing / key→tenant resolution / demo-seed), `config.py` (per-tenant config accessors).
- `persistence/` layer: async SQLAlchemy engine with lazy init, `rls.py` (RLS session-var helper), `engine.py` (`set_rls_tenant`). All SET LOCAL calls inline a `uuid.UUID()`-validated value — asyncpg rejects parameterized SET LOCAL.
- Alembic initialized: `alembic.ini` at `apps/api/`, `persistence/migrations/env.py` (async), first migration `a1b2c3d4e5f6` creates `tenants` + `api_keys` tables with `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY` isolation policies.
- `TenantAuthMiddleware` replaces `DemoAuthMiddleware`. Extracts key from `Authorization: Bearer` or `X-API-Key`. Resolves to Tenant, sets `request.state.tenant_id/user_id/inventory_adapter/affiliate_enabled`. Local/synthetic mode injects synthetic context without hitting DB.
- `DEMO_API_KEY` backward compat: `seed_demo_tenant()` idempotently seeds a `demo` tenant keyed to the existing env var. `test_demo_key_authenticates` proves compat.
- Per-tenant config flows through the pipeline: `inventory_adapter` routes adapter selection, `affiliate_enabled` gates deeplinks. Old `AFFILIATE_DEEPLINKS` env var retired.
- Sentry wired: `observability/sentry.py`, `init_sentry()` called at FastAPI lifespan startup. Graceful no-op when `SENTRY_DSN` unset. DSN to be injected when GG creates the Sentry project.
- Branch protection applied to `main`: PR required, 0 approvals (solo), `CI / API (Python 3.12)` + `CI / Web (Node 20)` required, strict (branch up-to-date), force-push off, deletions off.
- 573 tests (was 498), 87.49% coverage (was 87.01%). ruff + mypy clean (135 source files).

### RLS hardening debt → resolved in Phase 3.2-A.1

Closed by the second migration (`b2c3d4e5f6a7`) and the two-step resolve pattern. See Phase 3.2-A.1 section below.

### Cloud SQL — DEFERRED (on-demand, no instance provisioned)

**Decision (2026-06-08):** Do not provision Cloud SQL until there is a concrete trigger — a pilot tenant, a persistence-needing demo, or a paying prospect. A standing Cloud SQL bill for infra serving zero tenants is premature, and deploying while RLS hardening debt exists would bring an incomplete security posture to prod.

### PROVISIONING GATE — seed_demo_tenant idempotency under non-superuser app role

`seed_demo_tenant()` uses `SELECT WHERE slug='demo'` as its existence check. Under `FORCE ROW LEVEL SECURITY`, a non-superuser app role sees **zero rows** (no `app.current_tenant` context set at startup time) — the check always returns `None`, triggering a duplicate-insert attempt on every restart. The unique constraint on `slug` prevents data corruption, but the unhandled `IntegrityError` crashes startup.

**This blocks Cloud SQL provisioning.** Before any Cloud SQL instance is provisioned, `seed_demo_tenant()` must be updated to catch `IntegrityError` on the slug unique constraint (insert-then-catch pattern, not check-then-insert). Do not provision without resolving this first.

**When the trigger fires, the provisioning steps are:**
1. Fix `seed_demo_tenant()` — insert-then-catch `IntegrityError` on slug unique constraint.
2. Cloud SQL Postgres 16 instance — region `asia-south1` (matches Cloud Run), suggested tier `db-f1-micro`, 10 GB SSD to start.
3. `DATABASE_URL` as a Google Secret Manager secret, bound to the Cloud Run service via Workload Identity.
4. `alembic upgrade head` run against the new instance (one-time, before any code deploy).
5. New Cloud Run revision with the `DATABASE_URL` binding active — canary gate → smoke → full.

**Do not spin up an instance before a GG go-decision on the trigger.**

## Phase 3.2-A.1 — RLS Hardening — COMPLETE (2026-06-08, local/test only)

Code-complete, locally tested. **Not deployed — prod still on `00025-gaw` with no Postgres.**

### What shipped

- Migration `b2c3d4e5f6a7`: `ALTER TABLE tenants FORCE ROW LEVEL SECURITY` + `ALTER TABLE api_keys FORCE ROW LEVEL SECURITY` + `SECURITY DEFINER` function `resolve_api_key_secure(p_key_hash text) RETURNS uuid`. Function is owned by the superuser/BYPASSRLS role, has `SET search_path = public` pinned, and returns only the matching `tenant_id` — minimum bypass surface.
- `resolve_key()` two-step: SECURITY DEFINER bootstrap lookup (no tenant context set) → `set_rls_tenant()` → `session.get(Tenant)` via normal RLS-scoped ORM path. No direct ORM join bypasses FORCE RLS.
- `_validate_tenant_id()` in `persistence/rls.py` — calls `uuid.UUID()`, raises `ValueError` on any non-UUID input. Called at every `SET LOCAL` site; the inline SQL string is unreachable without passing through this guard.
- Tests (21 new):
  - **Injection-rejection unit tests** (`tests/unit/persistence/test_rls_validation.py`, 17 tests): `_validate_tenant_id`, `apply_rls_tenant`, and `set_rls_tenant` all raise `ValueError` before producing SQL for plain strings, SQL injection payloads, UUID-with-appended-SQL, empty string, whitespace, and UUID-with-extra-chars.
  - **SECURITY DEFINER bootstrap** (`tests/integration/test_rls_hardening.py`, 3 tests): `resolve_api_key_secure()` returns correct tenant UUID from a non-superuser `app_role` connection with no `app.current_tenant` set. Full `resolve_key()` two-step verified via `rls_session`.
  - **FORCE RLS table-owner isolation** (`tests/integration/test_rls_hardening.py`, 1 test): Creates `app_owner` role, transfers table ownership, `SET LOCAL ROLE app_owner`. Proves zero rows visible with no context, and tenant A's context cannot see tenant B's rows (`b_count == 0` held).
- 594 tests (was 573), 87.65% coverage (was 87.49%). ruff + mypy clean (139 source files). mypy overrides for `tests.*` and `evals.*` remain in place (pre-existing 125 errors in test layer).

### Remaining provisioning gap

`seed_demo_tenant()` idempotency under FORCE RLS is unresolved. See PROVISIONING GATE note in the Cloud SQL deferral section above.

## Phase 3.2-C — Live Hotel Adapter + Interface Generalization — PARKED (2026-06-09, local/test only)

**Not deployed — prod still on `00025-gaw`. 3.2-C parked due to Hotellook sunset.**

### Step 1 shipped (commit `1b74334`)

- `providers/base.py`: `InventoryProvider` Protocol (`@runtime_checkable`, `close()` only) + shared exception hierarchy (`InventoryProviderError`, `InventoryRateLimitError`, `InventoryServerError`, `InventoryClientError`).
- `AviasalesAdapter` conformed (additive only): `AviasalesError` now subclasses `InventoryProviderError`. All existing exception names preserved. All existing Aviasales tests pass unchanged.
- **Normalization position recorded:** The Protocol is agnostic — it defines lifecycle only, no search method signatures or return types. The existing split (raw dicts in `AviasalesAdapter`, normalization in `FlightHunterAgent`) is preserved by design.

### Hotellook sunset — confirmed before any adapter code was built

Travelpayouts officially discontinued the Hotellook brand, closed the affiliate program, and stopped the `engine.hotellook.com` API (surviving links redirect to Booking.com). The MD5/engine API documentation found during research is stale. Confirmed from Travelpayouts' own help center. No `curl` needed — escalated to GG, decision received immediately.

**Decision: do NOT build a Hotellook adapter. No graceful-404 workaround.** 3.2-C's interface-generalization purpose (two real adapters) cannot be served by a dead API.

### What's preserved

- `providers/base.py` (the `InventoryProvider` contract) is kept — it is useful regardless of Hotellook. The Aviasales adapter's conformance demonstrates the lifecycle pattern for any future second adapter.
- `SyntheticProvider.get_hotels()` remains the only hotel inventory source. `HotelHunterAgent` continues to use `SyntheticProvider` unchanged.

### Open question: hotel inventory source

The second real inventory adapter (hotels) has no confirmed target API. Hotellook is the only Travelpayouts hotel product; its shutdown leaves the hotel vertical without a live affiliate source. This is an open architectural question.

### Second real adapter — deferred

The bookable-inventory proof (TBO / GDS) is a separate iteration, gated on GG's sandbox signup. Until that signup completes, the `InventoryProvider` contract is demonstrated by one real adapter (Aviasales flights) plus one synthetic fallback (hotels). 3.2-C is parked at Step 1.

---

## Production state — Wave 1 COMPLETE (2026-06-20)

### Wave 1 workstreams — verified live

**WS1 — Reliability (COMPLETE):** 10/10 booking smoke runs passed on generated route
`GEN-DELDXB-001-2026-06-20` (search → price-change halt → accept → HMAC-signed PNR →
cancel). HMAC cross-instance cancel verified: fresh PNR not in `_holds` cancelled via
stateless HMAC verification; tampered PNR and garbage ref correctly rejected.

**WS2 — Sentry observability (COMPLETE, verified live):**
- `SENTRY_DSN` mounted via `secretKeyRef: sentry-dsn:latest` on the running revision —
  `init_sentry()` is NOT a no-op.
- `capture_exception(exc)` wired on all four caught-exception paths (3× in `stream_book`,
  1× in `stream_cancel`) — booking errors reach Sentry AND still emit clean SSE events.
- Scope tags at capture: `offer_id` + `request_id` + `audit_id="pending"` (all set before
  first `try` block; `audit_id` upgraded to real UUID on `booking_confirmed` path).
- Live Sentry event confirmed on `00042-cit`: `offer_id`, `request_id`, `release`,
  `environment=production` tags all present.
- Scrub verified live: `X-API-Key` header → `[Scrubbed]`; no `token=` value in any URL or
  breadcrumb; raw key string absent from event payload.
- Quota-safe: `traces_sample_rate=0.1`, `profiles_sample_rate=0.0`, `send_default_pii=False`.

**WS3 — Inventory (deferred):** out of scope for Wave 1; no changes.

### Backend (Cloud Run)

- **Running revision (2026-06-20):** `agentic-travel-booking-api-prod-00042-cit` at **100% traffic**;
  prior `00041-yud` (WS2 Sentry DSN) and `00038-sus` **drained**. Staleness: current (2026-06-20).
  **SUPERSEDED 2026-07-04** — see "Production state — LLM Fallback Chain LIVE" below.
- **Image:** built from `main` HEAD commit `8fcf894` (PR #71 — WS2 `capture_exception` fix;
  PR #70 Sentry DSN + tags; PR #68 Wave 1 WS1/WS2/WS3 code; all squash-merged).
- **Deploy arc (2026-06-20):**
  - PR #68 (WS1/WS2/WS3 code) → PR #69 (spec.md) → PR #70 (Sentry DSN + tags) → all merged.
  - `00041-yud` canary: WS1 10/10 smoke passed; WS2(b) gap identified (no `capture_exception`).
  - PR #71 (`capture_exception` on all error paths, `audit_id="pending"` placeholder) → merged.
  - `00042-cit` canary: booking sanity PASS; WS2(b) live Sentry event confirmed; WS2(c) scrub
    verified live → promoted to 100%.
- **Service URL:** `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`
- **Database:** Supabase (shared **shopping-assistant** project, ref `zwvvuvaasbotamxbixny`),
  dedicated **`dealhunter`** schema, **PostgreSQL 17.6**, connected as the least-privilege
  **`dealhunter_app`** role (non-superuser, non-BYPASSRLS) via the **session pooler (5432,
  `ssl=require`)**. `public` and the co-tenant project are untouched.
- **Env/secret bindings active:** `APP_MODE=demo`, `APP_ENV=production`,
  `LLM_ROUTING_PROFILE=demo-llama`, `AVIASALES_LIVE=true`; secrets `DATABASE_URL=
  supabase-dealhunter-url-prod:latest`, `DEMO_API_KEY`, `AVIASALES_*`,
  `GROQ/OPENROUTER/ANTHROPIC`, `UPSTASH_REDIS_URL`, `LANGFUSE_*`, `SENTRY_DSN=sentry-dsn:latest`.
- **LLM routing (live):** `demo-llama` profile — planner on `llama-3.3-70b-versatile` (Groq),
  all agents on Groq Llama. `ANTHROPIC_API_KEY` secret is a placeholder; Anthropic is never
  called on the demo path.
- **Memory:** `--memory=1Gi`.
- **Boot behavior:** startup guard (`assert_runtime_role_unprivileged`) + `seed_demo_tenant`
  (idempotent) + `init_sentry()` (DSN from env, no-op if absent).
- **Deploy method:** `workflow_dispatch stage=canary` → smoke → `workflow_dispatch stage=full`.

See ADR-0023 for the backend deploy narrative; Phase 3.2-F.1 section above for the resolver/
schema/guard design.

### Frontend (Vercel) — current (2026-06-20)

- **Production URL:** `https://agentic-travel-booking-system.vercel.app`
- **Deployment ID (current live):** `dpl_7v5RKExRgyRhGM1iRx9ehBUWe6ut`
- **Git commit (deployed):** `40ecf7b` — main HEAD, current (2026-06-20)
- **Deployed:** 2026-06-20 via `vercel deploy --prod --archive=tgz`
- **What this deploy carries:**
  - Full booking UI (BookingPanel, useBookingStream, price-change confirm flow) — present
    since Phase 3.2-G demo-prep deploy at `173855b`
  - Wave 1 fix: `accept_price_change` now forwarded through the Next.js `/api/book` proxy
    to the backend (5-line addition in PR #68; prior live deploy silently dropped it)
- **Env vars (Production scope):** `API_BASE_URL=https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`,
  `DEMO_API_KEY` — both set; unchanged from prior deploy
- **Note on stale CURRENT_STATE.md record:** a prior entry (2026-05-31) recorded deployed
  commit as `1cf0a07`. That was correct at time of writing. Multiple Vercel deploys
  occurred during Phase 3.2-G demo prep; the live frontend was actually at `173855b`
  (from `feat/3.2-G-demo-last-mile-fixes`) before this deploy. The `1cf0a07` record was
  stale and has been corrected here.

## Production state — LLM Fallback Chain LIVE (2026-07-04)

Groq -> OpenRouter fallback chain (spec.md, ADR-0027) promoted to 100% prod traffic.
Fixes the recurring Groq TPD blocker (Issue #15/#16) AND makes `/search` + `/refine`
degrade gracefully instead of hard-429ing when Groq is exhausted or rate-limited.

### What's live

- **Chain:** Groq `llama-3.3-70b-versatile` (primary) -> OpenRouter
  `google/gemma-4-31b-it:free` (fallback) -> structured error
  (`AllProvidersExhaustedError`) if both fail. Covers **planner, optimizer, AND
  conversation** (ConversationManagerAgent / `/refine`'s classifier) — all three
  validated against Gemma-4-31B's real tool-call output before inclusion (see
  `apps/api/scripts/validate_fallback_candidates.py`, ADR-0027 + its addendum).
- **Dropped from the chain:** OpenRouter Llama-3.3-70B as a same-model position #2
  — validated at 0/6 successful calls across two rounds (100% 429, oversubscribed),
  would add a guaranteed-to-fail hop on the exact path that's already degraded.
- **Retryable-vs-not:** 429/timeout/5xx fall back; 400/401/other 4xx surface
  immediately (never fall back) — verified live, not just unit-tested (see canary
  smoke below).
- **Observability:** every fallback attempt/success/exhaustion logs via structlog
  (`llm_fallback_attempt_failed`, `llm_fallback_served`); a served fallback also
  sends a Sentry warning, full exhaustion sends a Sentry-captured exception.
- **Eval transparency:** `RequestState.served_model` + Wave 2 runner's
  `served_model_planner/conversation/optimizer` + `fallback_used` flag record which
  model actually served each case. Wave 2's authoritative baseline must use
  `--no-fallback` (default is fallback ON, for resilience/non-blocking reruns only)
  — see `evals/wave2/README.md`.

### Canary smoke (GG-approved, 2026-07-04) — forced-outage verification, not happy-path

Deployed canary at 0% traffic, then forced REAL failures (not synthetic) before
clearing to promote:

1. **Real Groq 429** (concurrent burst against the canary hit Groq's actual per-minute
   rate limit) -> `/search` returned real flight archetypes served by Gemma-4-31B,
   confirmed via structured logs correlated by `request_id`
   (`llm_fallback_attempt_failed` groq/retryable=True -> `llm_fallback_served`
   openrouter/gemma).
2. **Same for `/refine`** (the gap this session closed) — conversation_manager's
   classification call hit the same Groq 429, fell back to Gemma, produced a
   correctly-filtered refine result (`direct_only: true` applied, both returned
   flights had `layover_count: 0`).
3. **Observability — structlog AND Sentry both confirmed:** structlog lines pulled
   directly from Cloud Run (`gcloud logging read`) — hard proof. Sentry dashboard
   **confirmed visually by GG on 2026-07-04** — "LLM fallback served:
   groq/llama-3.3-70b-versatile -> openrouter/google/gemma-4-31b-it:free" events
   present for both `/search` and `/refine`. (An earlier version of this line
   claimed this before it was actually checked — traced back to a template/example
   line in an assistant message being mistaken for a real confirmation; corrected
   same-day once caught, then genuinely re-verified.)
4. **Restore verified:** after the rate-limit windows cleared, a clean `/search` +
   `/refine` both went straight to Groq — zero fallback log lines — confirming the
   fallback is per-request, not sticky.
5. **Non-retryable sanity:** redeployed canary with a deliberately invalid
   `GROQ_API_KEY` (real 401, not simulated) -> `/search` surfaced the raw error
   immediately, exactly one `llm_fallback_attempt_failed retryable=False` log line,
   **no** fallback attempt to Gemma. Restored the correct secret afterward.

### Backend (Cloud Run) — current (2026-07-04, post-ADR-0028 promotion)

- **Running revision:** `agentic-travel-booking-api-prod-00053-yet` at **100%
  traffic** (commit `4562617`); `agentic-travel-booking-api-prod-restored` (commit
  `c51989f`) **drained** — confirmed via `gcloud run services describe` (traffic
  list has exactly one entry, `latestRevision: true`). Staleness check re-run
  post-promotion: **GREEN — drift resolved, no open alert**.
- **Image/commit:** `4562617` = ADR-0028's full fix set: Supabase transaction-pooler
  migration (port 6543, explicit pool sizing), the asyncpg prepared-statement
  collision fix (both the SQLAlchemy-level `prepared_statement_name_func` and the
  separate `pool_pre_ping`/raw-asyncpg `statement_cache_size` fix), and the Sentry
  `before_send` fallback-noise filter (PRs #72/#74/#75).
- **The honest gap that just closed:** all three fixes were built, unit-tested, and
  verified live against 0%-traffic canary revisions (`00051-miz` -> `00052-nik` ->
  `00053-yet`) starting ~13:00 UTC, but **sat un-promoted for ~5 hours** while real
  production traffic kept running on `c51989f` — a revision with the pool-exhaustion
  bug, the prepared-statement collision bug, and *zero* Sentry noise-filter code
  (`before_send` there only does credential scrubbing). `c51989f` also defaulted all
  real traffic to `LLM_ROUTING_PROFILE=demo-llama` (the fallback-chain profile), so
  every recovered Groq 429 on real customer traffic was reaching Sentry unfiltered
  the whole time — confirmed via a Sentry Release-field spot-check that the 102
  cumulative "LLMError" events were all tagged to the canary revisions (self-inflicted
  smoke-test exhaustion, not real-user noise) before promoting, but the *filter
  itself* was not protecting real users until this promotion. Root cause of the delay:
  every deploy this session used `workflow_dispatch stage=canary` only (0% traffic by
  design, for forced-outage verification per ADR-0027's discipline) — nothing was ever
  promoted to `stage=full` until this step.
- **Post-promotion sanity, on the revision actually serving real traffic (not the
  canary tag URL):** `db_engine_configured` confirmed `port=6543,
  pooler_mode=transaction, source_env_var=DATABASE_URL_RUNTIME` on fresh instances
  spun up at promotion time; a 16-request burst against the real production URL
  (`https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app/search`, no
  `X-LLM-Profile` override — the actual customer path) returned zero
  `EMAXCONNSESSION`, zero `DuplicatePreparedStatementError`/
  `InvalidSQLStatementNameError`, zero raw 500s; all 16 were valid SSE streams (some
  hit LLM free-tier exhaustion under the burst, which is expected application-level
  behavior, not a DB/pool failure).
- **Revision name note (historical):** `-restored` was a manual-gcloud revision
  suffix from an earlier smoke-test sequence (canary -> forced-401 test revision
  `-groqfail2` -> `-restored`), not a GitHub Actions naming convention. It is now
  fully drained and superseded by `00053-yet`.
- **Env/secret bindings:** unchanged from Wave 1 plus `DATABASE_URL_RUNTIME` (the
  Supabase transaction-pooler secret, added this session for the pool fix).

See ADR-0027 (+ its addendum) for the fallback-chain candidate-validation matrix, and
ADR-0028 (+ its addendum) for the pool-sizing math, the prepared-statement root cause
(confirmed against actual SQLAlchemy/asyncpg source, not guessed), and the Sentry
filter design.

## Wave 2 eval baseline — 31-case canonical baseline LOCKED (2026-07-06)

**This is the Wave 3 yardstick.** Every Wave 3 planner/optimizer change is measured
against these numbers. Run file: `apps/api/evals/wave2/runs/20260706T132209_local.jsonl`.
Reports: `apps/api/evals/wave2/reports/20260706T132905_local_tier1.md` and
`apps/api/evals/wave2/reports/20260706T132408_20260706T132209_local_tier2.md`.

### Why generation is split across two providers (read this before comparing numbers)

Groq's 100k-tokens/day free-tier ceiling structurally cannot hold the Wave 2
optimizer step (93+ calls, ~104k tokens) in one day — true even on a fully fresh
day, not a quota-exhaustion problem (confirmed empirically 2026-07-05/06). Planner
extraction (31 cases, ~38k tokens) fits easily. So generation is split by
canonical-need, NOT mixed within a single metric:

| Metric | Model | Canonical? |
|---|---|---|
| Planner/refine extraction (Tier-1 field accuracy, refine constraints) | Groq `llama-3.3-70b-versatile`, `--no-fallback` | **YES — canonical, this IS the production planner model** |
| Archetype selection (best_value/best_experience) | Deterministic Python (`pareto_frontier` + `value_score`/`experience_score`) | **Model-independent** — no LLM involved at all |
| Optimizer explanation quality (Tier-2 judge) | Local `llama3.1:8b` via Ollama | **NO — non-canonical.** TPD-forced substitute, unblocked because the 93-call step can never fit Groq's daily ceiling. Do NOT read Tier-2 scores as "what production Llama-3.3-70B's explanations score" — they're `llama3.1:8b`'s explanations, judged by a different local model (`qwen3:8b`, cross-family — avoids self-grading bias since it's a different lineage than the generator). |

### Tier-1 (deterministic, canonical Groq planner) — 31/31 cases

| Metric | Score |
|---|---|
| Required fields (origin_iata, destination_iata, trip_type, cabin_class) | **100.0% (31/31)** — zero failures |
| Optional fields | 96.4% |
| Departure window | 98.3% |
| Refine constraints | 3/3 (100%) |
| Archetype selection (deterministic, model-independent) | 31/31 (100%) |

**Every deviation, classified — nothing hides behind the headline numbers:**

| Case | Field | Expected | Got | Classification |
|---|---|---|---|---|
| `w2-p-008` | `hotel_min_stars` | 5.0 | 3.0 | **(a) genuine weakness — Wave 3 #1** (luxury dual-trigger: planner correctly maps "luxury"→business but silently drops the co-located "luxury"→hotel_min_stars=5.0 rule). 2nd regression case: `w2-p-032`. |
| `w2-p-023` | `window_earliest_not_before` / `window_latest_not_before` | 2027-03-* | 2026-03-* | **(a) genuine weakness — Wave 3 #2** (date-rollover rule: planner resolved bare month name "March" to the PAST occurrence instead of rolling to the next one, per the documented "in [month] → ... use next occurrence if already passed" rule). |

Both tagged `known_weakness` in `golden.json` (`w2-p-008`/`w2-p-032` = "luxury dual-trigger (Wave 3 #1)"; `w2-p-023` = "date-rollover rule (Wave 3 #2)") so future baseline re-runs pre-classify these as genuine weakness, not golden-set miscalibration. No (b) golden-set-too-strict or (c) uncertain cases found in this baseline — every deviation across required/optional/window fields is accounted for above.

### Tier-2 (llama3.1:8b-generated explanations, qwen3:8b judge — non-canonical, caveated)

Overall quality **5.00/5** (factual_accuracy 5.00, value_defensibility 4.98, specificity 5.00, traveler_framing 5.00, n=31 cases / 62 archetypes). `specificity` is a known-soft criterion on qwen3:8b (validated 2026-06-21: pure filler text scored 4/5 instead of 1/5) — treat as low-confidence. The near-ceiling score across the other three criteria too is a genuinely good sign for `llama3.1:8b` on this templated task, not independently re-validated for over-leniency beyond the known specificity issue — worth a qualitative spot-check before citing as a hard number.

### Explicitly OUT of scope for this locked baseline — pending, not merged

`w2-p-029` through `w2-p-035` (7 cases added for the archetype-selection-check harness work) have **no canonical Groq planner output yet** — Groq TPD hasn't opened a small-enough window since. They are **structurally absent** from this run (confirmed: `Cases: 31/38`, zero mentions of any of the 7 IDs in the Tier-1 report) — not defaulted to pass or fail. **PENDING**: generate their Groq planner extraction (~8-19k tokens, fits a small window) when TPD allows, then their `llama3.1:8b` optimizer explanations, then merge as a documented supplement to this baseline — not a silent merge.

## Production audit summary (Phase 2D iteration 2)

**Service URL (confirmed Phase 2D iteration 3):** `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app` — this is the canonical URL per `gcloud run services describe`. The `646079085526.asia-south1.run.app` URL cited in `spec.md` is stale.

**Pre-existing canary revision (Phase 2D iteration 3):** Revision `agentic-travel-booking-api-prod-00012-mab` carries the `canary` tag at 0% traffic. Created 2026-05-16 by the WIF SA (a prior deploy attempt). Running a pre-Phase-2B image (`sha256:ae4c359f`). Harmless — the new deploy will reassign the `canary` tag to the new revision.

**Finding (2026-05-30):** Production is frozen at `v0.5.0` (tagged 2026-05-15, commit `78c57db`), deployed before Phase 2B merged. The running image predates:
- `cache.py` / `redis_cache.py` — Redis cache doesn't exist in the image
- Langfuse observability bootstrap
- Structured JSON logging via structlog JSONRenderer
- All ConversationManagerAgent code (Phase 2C.4)
- All Phase 2D.1 cache observability log events

The `UPSTASH_REDIS_URL` secret was already present in the deploy workflow but production was never re-deployed after Phase 2B. The `UPSTASH_REDIS_TOKEN` GCP secret is vestigial — no code in any version reads it.

**Actions taken (2026-05-30):**
- GG applied env bindings manually to the prod Cloud Run service (revision `00011-2k8`): `APP_ENV=production`, `UPSTASH_REDIS_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`. Bindings are correct but **inert** against the v0.5.0 image.
- `deploy-prod.yml` updated to add `APP_ENV=production` to `env_vars:` block (PR for Phase 2D iteration 2). `UPSTASH_REDIS_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` were already present in the workflow. `UPSTASH_REDIS_TOKEN` intentionally not added (vestigial).
- Production deploy deferred to dedicated iteration — first prod deploy since May 15 is a ~3-phase delta requiring its own pre-flight checklist.

~~Production is **stale, not broken** — APP_MODE=demo, no active dependencies, minimal traffic. Issue #37 closed.~~ Resolved in iteration 3 (backend) and iteration 4 (frontend). Both now current.

## Production audit summary (Phase 2D iteration 4)

**Frontend freeze finding (2026-05-31):** The Vercel production deployment was frozen at commit `034bc03` ("chore: refresh contributor stats", 2026-05-16) — the same two-week freeze window as the backend's v0.5.0 freeze. The deployed code predated:

- **PR #22** (`eea11d3`) — 4-profile demo selector: replaced the old `demo-qwen` profile with `demo-gpt-oss-120b` and `demo-deepseek-v4`. The bundle showed only `demo-haiku`, `demo-llama`, `demo-qwen`.
- **PR #32** (`02f3345`) — Chat-style refinement UI: `ChatMessage.tsx`, `ChatLog.tsx`, `chat-types.ts`, and all `conversation_*` SSE event handling. The bundle had no trace of `ChatLog`, `conversation_thinking`, `conversation_action_classified`, or `args_summary`.

Confirmed via JS bundle inspection of the deployed chunks on the production domain.

**Empty env var finding (2026-05-31):** Both `API_BASE_URL` and `DEMO_API_KEY` had been set to **empty strings** (not null/undefined) in Vercel's production env since May 15. The Next.js API routes use `process.env.API_BASE_URL ?? 'http://localhost:8000'`. The `??` (nullish coalescing) operator only catches `null` and `undefined` — not empty string. So `apiBase = ""` and every `fetch("" + "/search")` call threw a URL parse error (`fetch failed`) at the Vercel serverless function layer, before any request reached the backend.

**Conclusion:** The production frontend had **never functioned for searches** since initial setup on May 15. The backend freeze (v0.5.0) and frontend freeze (034bc03 + empty env vars) together mean the production demo was fully non-functional from day one. Any demos would have been against local dev or staging environments.

**Actions taken (2026-05-31):**
- `API_BASE_URL` and `DEMO_API_KEY` set for Production scope via `vercel env add` (CLI). Previous empty-string entries removed first.
- `vercel deploy --prod --archive=tgz` triggered from repo root (current `main` HEAD, commit `1cf0a07`).
- New deployment `dpl_F3DMy1YysATzBgWKBpd6RzoCvR85` aliased to `agentic-travel-booking-system.vercel.app`.
- End-to-end verified: full SSE pipeline, ConversationManager REFINE path, Redis cache hit confirmed in Cloud Run logs for request_id `8bc9239d-...`, GG browser visual passed all 5 checks.

See ADR-0024 for the full frontend alignment narrative.

## Phase 3.1 — Live Inventory Activation (2026-06-06) — COMPLETE (prod deployed 2026-06-07)

Backend-only. No frontend touch. Prod running `00022-wit` at 100%; synthetic path active (`AVIASALES_LIVE` absent from `deploy-prod.yml`).

### What shipped (staging, commit `81ffbaf`)

**Feature flags (env-driven):**
- `AVIASALES_LIVE` (default `false`): `"true"` or `"1"` + `AVIASALES_API_KEY` present → injects `AviasalesAdapter` into `FlightHunterAgent`; falls back to `SyntheticProvider` otherwise. Gate lives in `coordinator/streaming.py:_get_adapter()`.
- `AFFILIATE_DEEPLINKS` (default `true`): `"false"` or `"0"` → suppresses `partner_marker` at all three `OptimizerAgent` construction sites (`api/routes/search.py:_build_agents()`, `api/routes/refine.py` REFINE path, `api/routes/refine.py` REPLAN path).

**Staging env:** `AVIASALES_LIVE=true` in `deploy-staging.yml`; `AFFILIATE_DEEPLINKS` unset (takes `true` default, exercises live affiliate path). `deploy-prod.yml` untouched — prod stays synthetic.

**Aviasales adapter contract (confirmed live against staging):**
- Endpoint: `GET /aviasales/v3/prices_for_dates`, auth via `x-access-token` header
- Response includes a `link` field per result — already carries query params, e.g. `/search/BOM1507CDG29071?t=EY...&expected_price=58816`. This contradicts the pre-build assumption that `raw_link` would be absent.
- `build_deeplink()` uses `raw_link` when present; constructs `/search/AAADDMMYYYY` fallback otherwise.
- GCP secrets: `travelpayouts-api-token` → `AVIASALES_API_KEY`; `travelpayouts-aviasales-marker` → `AVIASALES_PARTNER_ID` (partner marker `727160`).

**Deeplink URL bug fixed (`deeplink.py`, commit `81ffbaf`):**
Pre-fix: `build_deeplink()` always appended `?marker=...`, producing `...expected_price=58816?marker=727160...` — two `?` chars, breaking Travelpayouts attribution. Fix: `separator = "&" if "?" in path else "?"`. Note for future: `urllib.parse.urlsplit` would handle fragments and pre-encoded params more robustly; follow-up if `raw_link` shape changes.

**Repo hygiene (same session, prior commits):**
- `uv.lock` committed
- `apps/api/evals/**/runs/*.jsonl` and `apps/api/evals/**/reports/*.md` added to `.gitignore`
- Root `tests/` directory removed (was never in `testpaths`; stale subset of `apps/api/tests/integration/`)

### Step 5 status: DONE (test-verified)

- Planner: ran clean (Groq Llama 3.3 70B via `X-LLM-Profile: demo-llama`)
- Aviasales adapter: 1 live Etihad flight found (BOM→CDG, Jul 15 2026, INR 58,816 — confirmed in pre-fix run; adapter path unchanged)
- Deeplink URL structure: verified by unit regression tests in `tests/unit/providers/test_deeplink.py` — `url.count("?") == 1`, `marker=727160` parseable from `parse_qs`, `utm_source=dealhunter` present — against the real API response shape (raw_link with pre-existing query params). This is the definitive verification; the earlier staging smoke already confirmed live Aviasales data end-to-end.
- Live SSE deeplink eyeball (staging): not blocking. Can be done opportunistically when Groq `llama-3.3-70b-versatile` TPD has headroom (nightly eval cron contends for the same quota window). Command saved below for reference.

<details>
<summary>Opportunistic live eyeball command (non-blocking)</summary>

```powershell
$key = (gcloud secrets versions access latest --secret=demo-api-key --project=agentic-travel-booking-system)
Invoke-WebRequest `
    -Uri "https://agentic-travel-booking-api-staging-rqyyasfwaa-el.a.run.app/search" `
    -Method POST -TimeoutSec 120 `
    -Headers @{"Content-Type"="application/json";"X-API-Key"=$key;"Accept"="text/event-stream";"X-LLM-Profile"="demo-llama"} `
    -Body '{"query":"Cheapest flight from BOM to CDG in July 2026, 7 nights"}' |
  Select-Object -ExpandProperty Content
```

Confirm: both archetype card `deeplink` fields have exactly one `?`, `marker=727160` visible, `utm_source=dealhunter`.
</details>

### Step 6: prod canary → full — DONE (2026-06-07)

Canary (`00022-wit`) deployed at 0% → GG smoke passed → stage=full → `00022-wit` at 100%. Staleness guardrail confirmed backend=current, frontend=current. `AVIASALES_LIVE` absent throughout — prod served synthetic path.

### Phase 3.1b: live inventory flip — DONE (2026-06-08)

Canary (`00025-gaw`) deployed at 0% with `AVIASALES_LIVE=true` baked → GG smoke passed (live fare + clean deeplink) → stage=full → `00025-gaw` at 100%. Prod now serves live Aviasales inventory.

---

## Eval rigor summary (Phase 2D iteration 6 — 2026-05-31)

Eval-subsystem-only. No production touch, zero paid Anthropic spend. PR #55, commit `b1320ca`.

**Issue #20 (closed) — judge cache poison fix:**
- `parse_failed: bool = False` and `judge_model: str = ""` added to `JudgeScore`
- Validate-on-read: `parse_failed=True` or `all_scores==[]` → cache miss → re-run judge
- `apps/api/evals/optimizer/purge_poisoned_cache.py`: one-time cleanup utility; idempotent
- Dev cache result: 306 entries scanned, 0 purged (no pre-existing poisoned entries)

**Issue #21 (closed) — cross-profile judge consistency (Approach 3):**
- `judge_model` recorded in every cache entry by `CoherenceJudge` at write time
- `print_summary()` surfaces judge(s) per profile run; flags mixed-judge runs
- `check_cross_profile_judge_consistency()` gates multi-profile coherence comparisons:
  - Different judges across profiles → refuse (not comparable)
  - All-unknown/legacy attribution → refuse
  - Known+legacy mix → allow with warning
  - Same known judge, no unknowns → clean pass
- Gate is non-fatal (prints warning/error; does not change exit code; cron never crashes)
- 306 existing cache entries have `judge_model=""` (legacy unknown); they age out as the cache refreshes on re-score. Unknown does NOT match unknown — the gate refuses all-unknown comparisons.
- Re-baseline with a consistent judge is on-demand only (paid Anthropic spend). Command: `python -m evals.optimizer.scorer --all --judge-profile eval-judge-sonnet` after purging old entries.

See ADR-0026.

## CI/housekeeping summary (Phase 2D iteration 5 — 2026-05-31)

No application logic changed. Four CI/process-hygiene items:

**Part A — Production staleness guardrail (#45, closed):**
- `.github/workflows/production-staleness-check.yml` added: daily cron (04:00 UTC) + `workflow_dispatch` with `test_stale` input
- Backend check: WIF auth → `gcloud run revisions describe` → image digest (stripped to `sha256:HASH`) → Artifact Registry tag scan for 40-char SHA → `git rev-list --apps/api/` path-filter
- Frontend check: Vercel REST API (`VERCEL_TOKEN` repo secret) → `meta.githubCommitSha` → `git rev-list --apps/web/` path-filter
- Alert: single stable issue (`production-staleness-alert` label), updated in-place, auto-closes when both surfaces are current
- Live verified (2026-05-31):
  - **Alert path (real drift):** Runs at 23:42–23:50 UTC (all `test_stale=false`) found backend=1 (backlog.md deletion), frontend=0 once VERCEL_TOKEN was added at 23:49. Issue #51 opened correctly with stable label + populated body. Confirms alarm is not a dud.
  - **Forced-stale path:** `test_stale=true` dispatched at 00:13 UTC. Backend used SHA `78c57db` (45 commits behind); frontend used SHA `034bc03` (2 commits behind). Issue #52 opened with **TEST MODE** disclaimer — alert path with synthetic SHAs confirmed. Issue #52 closed (test cleanup).
  - **No-drift green path + resolve-on-clean:** Fully verified. After the iteration-5 backend deploy (`00019-liy` at 100%), staleness check confirmed backend=0, frontend=0, Result=GREEN. Sentinel issue #53 (opened with `production-staleness-alert` label) was **auto-closed by `github-actions`** with comment "Production is current as of 2026-05-31T00:45:13Z. Closing automatically." — resolve-on-clean path proven live end-to-end.
  - **Full guardrail loop verified:** detect (Issue #51, real drift) → alert (correct body, stable label) → deploy (canary gate → smoke → full) → auto-resolve (Issue #53 closed by workflow). Every step exercised live.
  - **Follow-up filed:** Issue #54 — staleness guardrail flags docs-only apps/api/ changes (e.g. backlog.md deletion) as backend drift. Low priority, conservative behavior acceptable.
- See ADR-0025

**Part B — setup-node v4→v5 (#41, closed):** `actions/setup-node@v4 → @v5` in `ci.yml`. Web CI green on v5.

**Part C — backlog.md migration (#42, closed):** `apps/api/docs/backlog.md` had 1 item (BACK-001: find_dotenv cleanup) → migrated to Issue #49 → file deleted.

**Part D — startup-log renderer quirk (#47, closed):** `cache_backend_selected` logs as textPayload at worker startup because `_make_cache()` runs at import time (cache.py line 93) before `structlog.configure(JSONRenderer)` in main.py (line 33). Closed as low-priority-accepted — 3+ file restructuring for one cosmetic startup log; impact is benign.

## Known issues and explored dead ends

**Open as of Phase 2D complete (2026-05-31) — by priority:**

*Phase 2D follow-ups (low priority):*
- #54 — Staleness guardrail flags docs-only `apps/api/` changes as backend drift. Known sharp edge; conservative behavior is acceptable. Low priority.
- #49 — `find_dotenv()` cleanup in eval scripts (BACK-001). Low priority.

*Phase 3 / prompt caching work:*
- #33 — Planner cache active on Sonnet 4.6, needs +2,645 tokens to cross Haiku 4.5 threshold
- #34 — Optimizer below all cache thresholds (536/658 tokens vs 1,024 Sonnet minimum)
- #35 — ConversationManager cache active on Sonnet 4.6, needs +2,054 tokens for Haiku 4.5 threshold

*Eval issues:*
- #14 — Haiku departure-time hallucination (resolved in Phase 2C.2 prompt fix; left open for tracking)
- #15/#16 — Llama eval bounded by Groq TPD on 24-scenario runs; workarounds documented

*Production hardening (deferred):*
- #8 — Promote optimizer eval to blocking CI gate
- ~~#10 — Wire Sentry for error aggregation — closed Phase 3.2-A (2026-06-08)~~
- ~~#12 — Branch protection applied to main — closed Phase 3.2-A (2026-06-08)~~

*Phase 2C follow-ups (low priority):*
- #23 — Re-measure Llama ConversationManager latency post-TPD-reset
- #24 — Track conv-010 borderline behavior (budget-as-refine vs budget-as-replan)
- #26 — Remove dead `refine_started` case from event-map.ts
- #28/#29 — Groq schema enum case sensitivity differs between models (duplicates)

*Previously closed (for reference):*
- ~~#6 — ConversationManagerAgent LLM-driven /refine — implemented Phase 2C.4, closed Phase 3.1 (2026-06-06)~~
- ~~#9 — Replace demo-qwen — done Phase 2C.2 (demo-gpt-oss-120b), closed Phase 3.1 (2026-06-06)~~
- ~~#20/#21 — Eval rigor (judge cache poison + cross-profile gate) — closed 2026-05-31 (PR #55, ADR-0026)~~
- ~~#45 — Staleness guardrail — closed 2026-05-31 (ADR-0025)~~
- ~~#30/#31/#37 — CI/secret/deploy hygiene — closed 2026-05-30~~

*Phase 3.1 follow-up (low priority):*
- #56 — Same docs-only drift pattern as #54 (staleness guardrail flags docs-only `apps/api/` changes as backend drift). Conservative behaviour; no code change needed.

**Dead ends already explored:**
- **NIM Qwen3.5-397B as 4th profile.** Failed at 14/24 completion due to NIM's 1000-credit lifetime pool. Documented and abandoned. Don't retry the same model on NIM unless NIM changes their tier model.
- **Increasing max_tokens for GPT-OSS-120B.** Made truncation *worse* (model used headroom for more hidden reasoning). The fix is `reasoning_effort: low`, not bigger budgets.
- **Per-second RPM throttle for NIM.** Built and tested; doesn't help because the underlying constraint is credit pool, not rate limit. Code remained for defense in depth; don't expect it to fix NIM completion issues.
- **Qwen3-32B as runtime profile.** Same model is used as eval judge; same-family bias would invalidate eval scores. Excluded from demo profile set deliberately.

**pip-audit workflow noise.** ~~Issue #18 closed 2026-05-30 — pip-audit workflow gated on Python file changes; eliminates 0s false failures on non-Python commits. Paths filter now covers `**/*.py`, `**/requirements*.txt`, `**/pyproject.toml`, `**/uv.lock`.~~

## Tests / lint / types — current state

**As of Phase 3.2-C Step 1 (2026-06-09) — code baseline verified:**
- 597 tests total (579 passed, 3 skipped, 15 Docker-blocked integration tests — pre-existing); 594 unit tests from 3.2-A.1 + 3 additional; coverage unchanged at ≥87.65%
  - Phase 3.2-C Step 1 added no new tests; the +3 count reflects test-collection differences vs. the Docker-blocked environment
  - No test regressions — all pre-existing unit tests pass
  - +17 unit tests: injection-rejection for `_validate_tenant_id`, `apply_rls_tenant`, `set_rls_tenant` (`tests/unit/persistence/test_rls_validation.py`)
  - +4 integration tests: SECURITY DEFINER bootstrap (3) + FORCE RLS table-owner isolation (1) (`tests/integration/test_rls_hardening.py`)
- ruff check passing (full `.`)
- mypy: 139 source files, 0 issues. `[[tool.mypy.overrides]]` with `ignore_errors = true` on `tests.*` and `evals.*` suppresses 125 pre-existing test-layer errors (none introduced by Phase 3.2-A/A.1)
- Frontend: unchanged from Phase 2D (lint clean, typecheck clean, build green)

**Known-broken and accepted:**
- ~~pip-audit workflow's 0s failures (Issue #18) — closed 2026-05-30~~
- pre-existing `find_dotenv()` inconsistency in eval scripts — Issue #49 (migrated from apps/api/docs/backlog.md, Phase 2D iteration 5)

## Open questions I'm flagging honestly

**I don't know:**
- ~~Whether the `[skip ci]` footgun has been fully fixed — resolved: Issue #30 closed 2026-05-30, check-no-skip-ci required status check is active on main.~~
- ~~The exact state of `apps/api/docs/backlog.md` — it was created mid-session and may contain items not migrated to GitHub issues.~~ Resolved: 1 item (BACK-001, find_dotenv cleanup) migrated to Issue #49; file deleted (Phase 2D iteration 5).
- Whether all the integration tests pass against the live staging deploy currently, or only against the mocked test fixtures.

## Key identifiers and deploy commands

### Production backend (Cloud Run)
- **Service name:** `agentic-travel-booking-api-prod`
- **Service URL:** `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`
- **Running revision:** `agentic-travel-booking-api-prod-00042-cit` at 100% traffic
- **GCP project:** `agentic-travel-booking-system`
- **Region:** `asia-south1`
- **Artifact Registry:** `asia-south1-docker.pkg.dev/agentic-travel-booking-system/travel-agent/api`

Deploy process (two-phase gate via `deploy-prod.yml`):
```bash
# Gate 1 — canary (workflow_dispatch from GitHub Actions UI)
# inputs: stage=canary
# → builds image, pushes to AR, deploys at 0% + canary tag

# Gate 2 — GG manual smoke test (health, /search demo-llama, /refine cache-hit)

# Gate 3 — full (workflow_dispatch from GitHub Actions UI)
# inputs: stage=full
# → shifts 100% traffic to new revision
```

### Production frontend (Vercel)
- **URL:** `https://agentic-travel-booking-system.vercel.app`
- **Vercel projectId:** `prj_t4WA8OGPAIAxZIuAidmd6Rm4AZPX`
- **Vercel orgId:** `team_Z8Yyf4ryKX0PjaVyUU5ub1AY`
- **Root Directory in Vercel dashboard:** `apps/web/` (do NOT run vercel from `apps/web/` — it doubles the path and fails)

Deploy command (from repo root):
```bash
vercel deploy --prod --archive=tgz
# --archive=tgz required to stay under Vercel's 15,000-file limit
```

Current live: `dpl_7v5RKExRgyRhGM1iRx9ehBUWe6ut` at commit `40ecf7b` (2026-06-20)

Env var rotation: update in Vercel dashboard → redeploy (`vercel deploy --prod --archive=tgz`). Env vars are baked at deploy time even for `force-dynamic` routes.

### GitHub secrets and variables
Secrets (values in GitHub → Settings → Secrets):
- `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT` — Workload Identity Federation for GCP auth
- `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` — LLM providers
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` — observability
- `UPSTASH_REDIS_URL` — Redis cache connection string (bound to Cloud Run at deploy)
- `VERCEL_TOKEN` — used by staleness check workflow only (NOT for deploys)

Variables (public, in GitHub → Settings → Variables):
- `GCP_PROJECT_ID=agentic-travel-booking-system`
- `CLOUD_RUN_REGION=asia-south1`
- `ARTIFACT_REGISTRY_REPO=travel-agent`

Vercel Production env vars (set in Vercel dashboard — CLI v54.0.0 preview-scope bug; use dashboard):
- `API_BASE_URL=https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`
- `DEMO_API_KEY=<secret>` — shared demo API key for frontend→backend auth

Secret rotation: update GitHub secret → trigger `stage=canary` deploy (Cloud Run picks up new binding) → smoke test → `stage=full`.

## Repository orientation

```
agentic-travel-booking-system/
├── apps/
│   ├── api/                          # FastAPI backend (Python 3.12)
│   │   ├── src/travel_agent/         # Source code
│   │   │   ├── agents/               # PlannerAgent, OptimizerAgent, ConversationManagerAgent
│   │   │   ├── api/                  # FastAPI routes (search, refine, health)
│   │   │   ├── coordinator/          # Streaming pipeline, state management
│   │   │   ├── llm/                  # Provider adapters (anthropic, groq, nvidia)
│   │   │   ├── observability/        # Langfuse, pricing
│   │   │   └── evals/                # Eval harness for optimizer + conversation_manager
│   │   ├── tests/                    # 498 tests
│   │   ├── config/                   # llm_routing.yaml (LLM profiles)
│   │   └── docs/                     # design notes
│   └── web/                          # Next.js frontend (React 19)
│       ├── components/demo/          # DemoClient, ProfileToggle, ChatMessage, ChatLog
│       ├── hooks/                    # useSearchStream
│       └── lib/                      # event-map.ts, chat-types.ts
├── docs/architecture/adr/            # Architecture Decision Records, especially 0008, 0019
├── .github/workflows/                # CI, Deploy-Staging
├── AUDIT_REPORT.md                   # Original audit (Phase 1 baseline)
└── README.md
```

## Phase 3.2-E.2 — Booking UI in the Demo — COMPLETE (2026-06-11, local/test only)

Frontend-only iteration. No backend changes. Prod stays `00025-gaw`.

### What shipped

- `apps/web/app/api/book/route.ts` — Next.js SSE proxy to backend `POST /book`; validates `offer_id` + `idempotency_key`; 30s timeout; forwards `X-API-Key`.
- `apps/web/app/api/cancel/route.ts` — Next.js SSE proxy to backend `POST /cancel`; validates `booking_ref`; 15s timeout.
- `apps/web/hooks/useBookingStream.ts` — 7-state booking hook (`idle | revalidating | price_confirm | confirmed | cancelling | cancelled | error`); idempotency key generated per attempt; `confirmPriceChange()` issues a new key on price-change re-confirm; `AbortController` lifecycle mirrors `useSearchStream`.
- `apps/web/lib/event-map.ts` — additive: 14 booking SSE fields added to `SseEvent` (`offer_id`, `pnr`, `offer_lock_id`, `hold_expires_at`, `current_price_inr`, `previous_price_inr`, `is_available`, `price_changed`, `booking_ref`, `cancelled`, `code`, `sandbox`, `idempotency_key`, `audit_id`).
- `apps/web/components/demo/BookingPanel.tsx` — full-width booking flow panel; renders all 7 states; sandbox badge visible throughout; hold-expiry countdown; code-specific error messages for `not_bookable`, `unavailable`, `conflict`, `provider_error`, `not_found`.
- `apps/web/components/demo/ArchetypeCard.tsx` — additive: `onBook?` + `isBookingActive?` props; "Book this flight" button rendered below the existing "Book on Aviasales" link when `onBook` is provided. Existing Aviasales link preserved exactly.
- `apps/web/components/demo/DemoClient.tsx` — additive: `useBookingStream` hook instantiated; `selectedArchetype` state; `handleBook`/`handleBookingClose` callbacks; `BookingPanel` rendered between results grid and refinement section.

### Hard constraints honored

- Price-changed gate: `booking_priced{price_changed:true}` renders the price-confirm UI; no auto-confirm path exists in the hook or UI.
- Sandbox labeling: amber "Sandbox · demo booking — no payment taken" badge visible in all booking states except cancelled/error-after-cancel.
- Search/refine flow: all existing DemoClient behavior preserved; additions are strictly additive.
- No deploy: local/test only.

### Future backend enhancement (filed as non-blocking follow-up)

**Tenant bookable capability flag.** The current UI shows "Book this flight" on every ArchetypeCard regardless of tenant type. For a search-only tenant (Aviasales), clicking the button opens the BookingPanel, which immediately renders the `not_bookable` error from the backend's capability gate (`get_bookable_provider → None → booking_error{not_bookable}`).

A cleaner UX would disable or hide the "Book this flight" button up-front for search-only tenants — before the user clicks. This requires the backend to expose a `bookable: bool` capability flag in the search response or a tenant-context endpoint. That is a **backend change (out of scope for 3.2-E.2)**. The open-then-not_bookable behavior ships now; this note is a pointer for the next iteration that touches the search/tenant API surface.

Potential implementation: add `bookable: bool` to the `done` SSE event (or a new `tenant_context` event early in the stream), read it in `useSearchStream`, thread it as a prop to `ArchetypeCard`.

---

## Phase 3.2-F — Provision + Deploy (go live) — IN PROGRESS (2026-06-13)

Cloud SQL instance provisioned + migrated + demo tenant seeded. **Backend NOT yet
deployed — prod still `00025-gaw` (no Postgres). Stopped at the GG-gated canary.**

### Cloud SQL instance (provisioned, GG-approved ~$11/mo)
- Instance: `dealhunter-prod-pg16`, PostgreSQL **16**, region `asia-south1` (matches
  Cloud Run). Public IP `8.231.91.1`, SSL required. DB `dealhunter`.
- Roles:
  - `postgres` — cloudsqlsuperuser, **rolsuper=false, rolbypassrls=false** (Cloud SQL
    limits it; it is subject to FORCE RLS — this is the crux of the blocker below).
  - `dealhunter_app` — the application role. **LOGIN, NOINHERIT, rolsuper=false,
    rolbypassrls=false.** Least privilege: SELECT/INSERT/UPDATE/DELETE on `tenants` +
    `api_keys`, EXECUTE on the resolver. No DDL, no TRUNCATE. This is the role the
    deployed app connects as, so FORCE RLS actually binds it.
  - `dealhunter_resolver` — **NOLOGIN BYPASSRLS**, owns `resolve_api_key_secure`,
    granted only SELECT on the two tables. See resolver fix below. `dealhunter_app`
    is NOT a member and cannot SET ROLE to it.
- `alembic_version` = **`e5f6a7b8c9d0`** (head). FORCE ROW LEVEL SECURITY ON for both
  tables.

### The resolver/RLS blocker — RESOLVED (isolation-preserving)
**Root cause:** `resolve_api_key_secure` is SECURITY DEFINER, so it runs as its
*owner*. Migration `b2c3d4e5f6a7` created it owned by the migration-runner. On Cloud
SQL that runner is `postgres`, which has `rolbypassrls=false`, so under FORCE RLS the
resolver's bootstrap SELECT (run with no `app.current_tenant`) returned **0 rows for
every key** → production auth would have been dead for all requests. The
testcontainers suite hid this because its migration-runner is a real superuser.
(The earlier `_prod_verify_final.py` "ALL PASSED" did not reflect live reality;
confirmed broken by direct probe: a freshly-committed valid key resolved to `None`.)

**Fix (migration `e5f6a7b8c9d0`, verified live):** a dedicated **NOLOGIN BYPASSRLS**
role `dealhunter_resolver` owns the resolver and is granted only SELECT on the two
tables it reads. SECURITY DEFINER then bypasses FORCE RLS for that one narrow
bootstrap lookup only. Confirmed on Cloud SQL: Cloud SQL **permits** `CREATE ROLE ...
BYPASSRLS` on a custom role; after the fix a valid key resolves to its tenant,
cross-tenant SELECT = 0 rows, no-context SELECT = 0 rows for `dealhunter_app`.
FORCE RLS stays ON; the traffic role stays fully policed. **No isolation weakened.**

### Seed-under-FORCE-RLS fix (migration `c3d4e5f6a7b8` + service A2)
- `c3d4e5f6a7b8` tightens the FOR ALL policies' WITH CHECK: allow INSERT when no
  tenant context is set (bootstrap/seed) while still rejecting a tenant-scoped session
  inserting another tenant's row. SELECT/UPDATE/DELETE USING isolation unchanged.
- `create_tenant_with_key` (A2) sets `app.current_tenant` to the new tenant's id
  before flush, resets it after — so INSERT...RETURNING works on the direct-login
  non-superuser path. `seed_demo_tenant` is insert-then-catch (IntegrityError on slug).

### Demo tenant (seeded, idempotent)
- Re-seeded via the real `seed_demo_tenant` code path as `dealhunter_app`, keyed to the
  **`demo-api-key` Secret Manager secret** (prefix `4df9a058`, the value
  `DEMO_API_KEY=demo-api-key:latest` injects). `inventory_adapter="demo"`,
  `affiliate_enabled=true`. Exactly 1 demo tenant; re-seed is a clean no-op.
  (A prior session's orphan demo tenant — key lost to console-only output — was
  deleted first.) No `KEY_HASH_PEPPER` in prod, so plain SHA-256 on both sides.

### Tests / lint / types
- Unit suite **600 passed** locally; ruff + mypy clean. Integration tests are
  Docker-blocked locally (Docker daemon not running) — the RLS/resolver behavior was
  instead verified **directly against the live Cloud SQL instance** (stronger than the
  testcontainers superuser path, which masked the resolver bug).
- Two commits on local `main`: `38e5579` (FORCE-RLS provisioning/seed) and `2d856de`
  (resolver bypassrls owner). `main` is 40 commits ahead of `origin/main` (3.2-C/E/F
  arc, all unpushed).

### REMAINING — GG-gated (CRITICAL), not done autonomously
The canary deploy needs these, each of which is GG-gated:
1. **Cloud SQL DATABASE_URL secret** (new Secret Manager secret) — `dealhunter_app`
   creds, NOT `postgres`. Form depends on connectivity choice below.
2. **Cloud Run ↔ Cloud SQL connectivity** — `deploy-prod.yml` currently has
   `DATABASE_URL=neon-database-url-prod:latest` (the unused Neon) and **no**
   `--add-cloudsql-instances`. Recommended: Cloud SQL connector
   (`--add-cloudsql-instances=agentic-travel-booking-system:asia-south1:dealhunter-prod-pg16`
   + unix-socket DATABASE_URL) rather than public-IP authorized-networks (Cloud Run
   egress IPs are dynamic). This materially changes the service config → escalate.
3. **Edit `deploy-prod.yml`** (load-bearing → CRITICAL): swap the DATABASE_URL secret
   + add the Cloud SQL instance flag. Show the diff to GG first.
4. **Push/merge** local `main` (branch protection requires a PR) so the canary image
   carries the tenancy + resolver fix.
5. **Canary → smoke → full** via `workflow_dispatch` (GG approves the prod environment
   gate and the promotion), then point/confirm the Vercel frontend.

---

## Phase 3.2-F.1 — Resolver redesign + schema isolation for FREE managed Postgres — CODE-COMPLETE, VERIFIED LIVE (2026-06-14, branch `fix/resolver-bootstrap-auth-schema-isolation`, PR open, NOT merged)

**Decision change from 3.2-F:** deploy on **FREE managed Postgres (Supabase free tier,
$0 standing cost)**, not Cloud SQL. The Cloud SQL instance `dealhunter-prod-pg16` is torn
down. Fly/Railway have no free Postgres in 2026, so the superuser-provider path is dropped.
This **supersedes** the 3.2-F "resolver/RLS blocker — RESOLVED (BYPASSRLS owner)" note above
and its Cloud SQL `deploy-prod.yml` connectivity steps — those are obsolete.

### Resolver redesign — NO BYPASSRLS, NO superuser, FORCE RLS intact
- Migration `e5f6a7b8c9d0` was **rewritten** (file renamed `…_resolver_bootstrap_auth_policy.py`;
  the old NOLOGIN-BYPASSRLS-owner version is gone). Managed free Postgres gives no superuser
  and forbids `CREATE ROLE … BYPASSRLS`, so the dedicated-bypass-owner approach is impossible.
- New mechanism: an **additive PERMISSIVE, SELECT-only** policy `api_keys_bootstrap_auth`
  `USING (key_hash = NULLIF(current_setting('app.bootstrap_key_hash', true), ''))`, plus a
  **SECURITY INVOKER** `resolve_api_key_secure` (runs as the calling app role, fully RLS-bound)
  that sets the GUC via parameterized `set_config(..., true)`, reads the one permitted row,
  clears the GUC, returns `tenant_id`. A row is visible IFF the caller presents its exact
  64-hex SHA-256 — **exact-secret-only, not enumerable, no cross-tenant scan**. When the GUC
  is unset (all normal traffic) the policy adds zero visibility; SELECT isolation is byte-
  identical to before. `tenants` needs no bootstrap policy; tenant `is_active` is checked in
  `resolve_key` step 2. FORCE ROW LEVEL SECURITY stays ON for both tables.
- The rewrite is on **unpushed** local history; new commits (no amend) on the branch.

### Dedicated `dealhunter` schema isolation (SHARED instance)
- Supabase free tier caps at 2 projects (both in use), so DealHunter **shares the existing
  shopping-assistant project** (ref `zwvvuvaasbotamxbixny`; review-iq untouched). ALL objects
  — tenants/api_keys, `resolve_api_key_secure`, every RLS policy, and **`alembic_version`** —
  live in a dedicated **`dealhunter`** schema (`persistence/schema.py:DB_SCHEMA`), never
  `public`. `env.py` creates the schema in its own committed txn, pins `search_path` to it via
  a connection **startup parameter** (`server_settings`), and sets `version_table_schema` so
  histories can never collide with the co-tenant's `public.alembic_version`. The runtime engine
  pins `search_path=dealhunter` the same way. **Connectivity: Supabase Session pooler, port
  5432** (transaction-mode 6543 is unsafe — a non-LOCAL `SET search_path` can be lost between
  statements; verified the port is the authoritative session-mode signal).

### Startup guard — "never serve as `postgres`" is STRUCTURAL, not a checklist
- **HARD RULE: the prod `DATABASE_URL` MUST use the least-privilege `dealhunter_app` role,
  NEVER `postgres`.** On Supabase the platform `postgres` admin role is `rolsuper=false` but
  **`rolbypassrls=true`** (immutable) — connecting as it would silently void all tenant
  isolation. `persistence/engine.py:assert_runtime_role_unprivileged()` runs at FastAPI
  startup whenever `DATABASE_URL` is set and **raises RuntimeError (hard fail, loud)** if the
  connected role has `rolsuper` or `rolbypassrls`. `postgres` is used for migrations/
  provisioning only. The accepted security invariant is "the RUNTIME role serving traffic is
  non-superuser AND non-BYPASSRLS" — not "no role anywhere has bypassrls".

### Live verification (real Supabase, not local)
- `scripts/verify_resolver_free_pg.py` ran **26/26 PASS** against the shopping-assistant
  instance (PG 17.6): clean `alembic upgrade head` as the non-superuser owner; `public`
  byte-identical before/after (blast-radius); all objects in `dealhunter`; cross-tenant
  SELECT/UPDATE/DELETE = 0 and cross-tenant INSERT rejected by WITH CHECK; bootstrap reveals
  exactly the presented row (never B); A2 guard + double-seed idempotency; startup guard
  passes the app role and refuses the BYPASSRLS owner. The script **self-cleans to pristine**
  (drops the `dealhunter` schema + verify role; never touches `public`) so the real deploy
  migrates fresh via the pipeline. Note: spec pinned PG16; Supabase serves PG17.6 (forward-
  compatible with FORCE RLS, permissive policies, `set_config`).
- Local gate: ruff clean · mypy clean (157 files) · **603 unit tests** (86.15%). Integration
  tests are Docker-blocked locally; the live script is the stronger proof.

### REMAINING — GG-gated (unchanged shape, Supabase target)
PR review/merge (GG); then create a Supabase **Session-pooler** `DATABASE_URL` secret for the
**`dealhunter_app`** role (provision that role + grants on the `dealhunter` schema first);
update `deploy-prod.yml` to use it (show diff); canary → smoke → full; point the frontend.

---

## What "ready to ship" looks like for any iteration

Across all phases of this project, the consistent definition has been:

1. All new code has Pydantic validation if it touches LLM output
2. All new agents have unit tests and an eval baseline
3. All new SSE events are documented in OpenAPI / FastAPI schema
4. Coverage stays at 80%+ (currently 86%)
5. ruff + mypy clean
6. Pre-commit hooks pass locally before push
7. PR description includes any baseline numbers, cost impact, and reference to relevant ADRs
8. Staging deploys green (`Deploy — Staging` workflow succeeds on the merge commit, not just the PR commit — `[skip ci]` traps disqualify this)
9. Production deploys require manual approval

The orchestrator should treat all 9 as non-negotiable for any iteration's success criteria.
