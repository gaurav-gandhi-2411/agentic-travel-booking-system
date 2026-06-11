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

## Production state (Phase 3.1b deployed — 2026-06-08)

Both surfaces are **fully current**. Backend carries Phase 3.1 code (AVIASALES_LIVE flag wiring, deeplink separator fix); prod runs **live Aviasales inventory** (`AVIASALES_LIVE=true` baked into `deploy-prod.yml`). Frontend unchanged.

### Backend (Cloud Run)

- **Running revision:** `agentic-travel-booking-api-prod-00025-gaw` at 100% traffic
- **Image:** built from `main` HEAD at commit `4f0c02f` (Phase 3.1b — AVIASALES_LIVE=true in deploy-prod.yml)
- **Git equivalent:** fully current with main; 0 commits behind in `apps/api/`
- **Deploy (Phase 3.1b, 2026-06-08):** stage=canary (`00025-gaw` at 0% + tag) → GG canary smoke passed (live fare + clean deeplink) → stage=full (`00025-gaw` at 100%).
- **Service URL:** `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`
- **Env bindings active:** `APP_ENV=production`, `UPSTASH_REDIS_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `AVIASALES_API_KEY`, `AVIASALES_PARTNER_ID`, `AVIASALES_LIVE=true` — all bound. Live inventory active.
- **Deploy method:** `workflow_dispatch stage=canary` (Gate 1) → human smoke test → `workflow_dispatch stage=full` (Gate 2 after GG approval)

See ADR-0023 for the full backend deploy narrative.

### Frontend (Vercel)

- **Production URL:** `https://agentic-travel-booking-system.vercel.app`
- **Deployment ID:** `dpl_F3DMy1YysATzBgWKBpd6RzoCvR85`
- **Git commit:** `1cf0a07` (current `main` HEAD, includes PRs #22 and #32)
- **Deployed:** 2026-05-31 via `vercel deploy --prod --archive=tgz` (Vercel CLI, authenticated as `gaurav-gandhi-2411`)
- **Env vars (Production scope):** `API_BASE_URL=https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`, `DEMO_API_KEY` — both set correctly; prior deployment had both as empty strings (root cause of broken searches)
- **Post-deploy verified (orchestrator + GG browser visual):**
  - Bundle: `conversation_thinking`, `conversation_action_classified`, `args_summary`, all 4 chat kinds, all 4 profiles present; `demo-qwen` gone
  - `/api/search` → full SSE pipeline reaching prod Cloud Run (confirmed via `search_cache_put_success` in Cloud Run logs)
  - `/api/refine` → `conversation_thinking` → `conversation_action_classified` (action=refine, args_summary LLM-generated) → Redis cache hit → archetypes
  - GG browser: 4-profile selector, progress feed, archetype cards, chat bubbles (user/thinking/action/message), NO_OP conversation_message, zero console errors

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
- **Running revision:** `agentic-travel-booking-api-prod-00019-liy` at 100% traffic
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
