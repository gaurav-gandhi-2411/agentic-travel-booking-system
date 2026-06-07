# Project Spec: DealHunter — Phase 3.2-A (Tenancy Foundation)

## Goal

Stand up the multi-tenancy spine: per-tenant API-key authentication, a durable
Postgres tenant store with row-level security (RLS) for isolation, the `tenancy/`
module, and per-tenant configuration (inventory-adapter selection, affiliate
on/off, rate-limit tier as a stored field). Tenant context (`tenant_id`,
`user_id`) flows through the pipeline via `RequestState`.

This is the foundation every later 3.2 iteration depends on. It does NOT add
metering enforcement (Iteration B) or a second inventory adapter (Iteration C).

**This iteration is code-complete + locally tested ONLY — no cloud deploy.**
Prod is currently serving live Aviasales traffic on revision `00025-gaw` and has
no Postgres. Provisioning a cloud database (Cloud SQL) is a separate, GG-gated
decision after this code is green.

## Current state (existing project)

- Prod backend: Cloud Run `00025-gaw` at 100%, **live Aviasales inventory**.
- Auth today: `DemoAuthMiddleware` (`api/middleware/auth.py`) — a single shared
  `DEMO_API_KEY` gate. This key authenticates current staging + prod demo traffic
  and MUST keep working.
- `tenant_id`/`user_id` exist in `RequestState` (`coordinator/state.py`) but are
  never populated. `tenancy/__init__.py` is empty.
- Per-tenant affiliate control already half-exists: `AFFILIATE_DEEPLINKS` is a
  flag; this iteration makes it a per-tenant config value.
- Persistence today: only Upstash Redis (cache). No relational store, no ORM.
- 498 tests / 87.01% coverage, ruff + mypy clean.

### Load-bearing — do NOT touch without escalating
- `config/llm_routing.yaml`
- `agents/optimizer.py` (system prompt), `agents/conversation_manager.py`
- `evals/optimizer/thresholds.py`, `runner.py`
- `llm/` adapters
- `.github/workflows/deploy-*.yml` (and not deployed this iteration regardless)

## Scope

### In scope
- Postgres schema + Alembic migrations; async SQLAlchemy 2.0 models.
- `tenants` and `api_keys` tables (keys stored **hashed**, never plaintext),
  with per-tenant config fields: `inventory_adapter`, `affiliate_enabled`,
  `rate_limit_tier` (stored, NOT enforced this iteration).
- RLS policies enforcing tenant isolation; per-request session var
  (e.g. `SET app.current_tenant`) set from the authenticated tenant.
- Per-tenant API-key auth: key generation util, hashed storage, key→tenant
  resolution middleware replacing `DemoAuthMiddleware`.
- **Backward compat:** seed a `demo` tenant whose key is the existing
  `DEMO_API_KEY`, so all current traffic keeps authenticating unchanged.
- Populate `tenant_id`/`user_id` in `RequestState` from the resolved tenant.
- Consume per-tenant config in the pipeline: adapter selection + `affiliate_enabled`
  drive `_get_adapter()` / the deeplink path (a tenant can be affiliate-OFF).
- Sentry (#10): wire `sentry-sdk[fastapi]`, DSN via env/secret, no-op if unset.
- Branch protection (#12): propose a ruleset for `main`; apply ONLY on GG approval.

### Out of scope (do NOT build)
- Per-tenant quota/rate-limit **enforcement** (Iteration B — store the tier only).
- Second inventory adapter / interface generalization (Iteration C).
- Booking, payments, PII scrubbing (Iteration E).
- Any cloud deploy or Cloud SQL provisioning (separate GG-gated step).
- Frontend changes.

## Tech stack (additions authorized by this spec)
- Python 3.12, FastAPI (existing)
- SQLAlchemy 2.0 (async) + asyncpg — relational layer
- Alembic — migrations
- Postgres 16 — **local/docker for dev + test this iteration** (no cloud)
- `sentry-sdk[fastapi]` — error aggregation
- `passlib[argon2]` (or stdlib hashing) — API-key hashing

## Architecture (new dirs allowed: tenancy/, db/, migrations/)
```
apps/api/src/travel_agent/
├── tenancy/
│   ├── models.py        # Tenant, ApiKey (SQLAlchemy)
│   ├── service.py       # key generation, hashing, key->tenant resolution
│   └── config.py        # per-tenant config accessors
├── db/
│   ├── engine.py        # async engine + session, RLS session-var wiring
│   └── rls.py           # policy helpers
├── api/middleware/auth.py   # replace DemoAuthMiddleware with tenant-key auth
├── coordinator/state.py     # populate tenant_id/user_id (do not change schema)
└── migrations/              # Alembic
```

## Verification commands
```yaml
- name: tests
  cmd: pytest -q
  required: true
- name: lint
  cmd: ruff check .
  required: true
- name: types
  cmd: mypy .
  required: true
```
Tests run against a local/docker Postgres. Include a cross-tenant RLS isolation
test (tenant A cannot read tenant B's rows) and a demo-key backward-compat test.

## Subagent usage rules
- `executor` for code; `verifier` for tests/lint/types.
- Orchestrator does NOT write code.

## Escalation rules (orchestrator must ask before doing)
- Ask before provisioning ANY cloud Postgres / Cloud SQL — that's a cost + region
  + sizing decision for GG. This iteration is local/test only.
- Ask before ANY prod or staging deploy — neither has Postgres; do not deploy.
- Ask before applying the branch-protection ruleset to `main` (changes merge rules).
- Ask before installing any dependency beyond the stack listed above.
- Escalate if backward compat breaks — the existing `DEMO_API_KEY` must keep
  authenticating (as the seeded demo tenant). Show the compat test passing.
- Escalate if any existing (498) test fails.

## Hard rules
- Do NOT break live prod auth: `DEMO_API_KEY` continues to authenticate.
- Do NOT deploy this iteration (no cloud DB). Prod stays on `00025-gaw`.
- Store API keys **hashed** only — never plaintext, never in commits/logs.
- Do NOT set `ANTHROPIC_API_KEY` (GG on Claude Max — double-bills).
- Do NOT implement metering enforcement or a second adapter.
- Do NOT touch load-bearing files or the frontend.
- Run the full existing suite after every executor pass; escalate on any new failure.

## Budget
- Soft target: 1 CC session.
- Hard cap: stop and escalate after 20 executor invocations.
- Orchestrator runs `/cost` at midpoint and reports.

## Success criteria (verify ALL before declaring done)
- `tenancy/` implemented: Tenant + ApiKey models, key→tenant resolution,
  per-tenant config (adapter, affiliate_enabled, rate_limit_tier).
- Per-tenant API-key auth replaces the shared-key middleware; keys stored hashed.
- Existing `DEMO_API_KEY` still authenticates as a seeded `demo` tenant
  (backward-compat test passes).
- `tenant_id`/`user_id` populated in `RequestState` from the resolved tenant.
- Postgres schema + Alembic migrations apply cleanly; async SQLAlchemy models.
- RLS enforces isolation — cross-tenant isolation test proves A cannot read B.
- A tenant configured `affiliate_enabled=false` suppresses affiliate deeplinks;
  `true` restores them (test).
- Sentry wired (no-op when DSN unset).
- Branch-protection ruleset applied to `main` (after GG approval) or left
  proposed-and-pending if GG defers.
- All tests pass (count grows), coverage ≥ 86%, ruff + mypy clean.
- No cloud deploy; prod still `00025-gaw`.

## Build order
1. Add deps; stand up local docker Postgres for dev/test; async SQLAlchemy engine
   + session; `alembic init`.
2. Tenant data model + first migration (`tenants`, `api_keys`, config fields);
   RLS policies + per-request session-var wiring.
3. API-key auth: generation + hashing + key→tenant resolution; replace
   `DemoAuthMiddleware`; seed `demo` tenant from existing `DEMO_API_KEY`
   (backward compat). Populate `RequestState`.
4. Per-tenant config consumption: adapter selection + `affiliate_enabled` drive
   the pipeline / deeplink path.
5. Tests: auth (valid/invalid/demo-compat), cross-tenant RLS isolation,
   affiliate-off by config, RequestState population. Verifier pass.
6. Sentry wiring (code-complete; DSN injected later — GG creates the Sentry project).
7. Propose branch-protection ruleset for `main`; apply on GG approval.
8. Report. Surface the Cloud SQL provisioning decision as the next gate. Do NOT deploy.
```
