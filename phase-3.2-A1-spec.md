# Project Spec: DealHunter — Phase 3.2-A.1 (RLS Hardening)

## Goal

Close the superuser/table-owner RLS bypass gap that was intentionally deferred in
Phase 3.2-A. After this iteration, row-level security is unconditional:

1. `FORCE ROW LEVEL SECURITY` on all tenant-owned tables — the table owner role
   is now subject to the same policies as any other role.
2. The one legitimate bypass — the auth bootstrap query (key→tenant resolution
   before any tenant context exists) — is replaced with a `SECURITY DEFINER`
   Postgres function that targets only the minimum data needed and nothing else.
3. The `SET LOCAL` UUID-inlining path has a hard boundary: invalid or
   injection-shaped values are rejected by the Python layer before they ever
   reach SQL. A test proves this.
4. A test proves RLS isolation holds on a table-owner connection (i.e., FORCE
   RLS is actually doing what it claims).

**Local/test only — no cloud deploy. Prod stays on `00025-gaw`.**

## Current state

- Phase 3.2-A complete: `tenants` + `api_keys` tables, `ENABLE ROW LEVEL
  SECURITY`, isolation policies using `app.current_tenant` session variable.
- `ENABLE` (without `FORCE`) means the table owner (and any superuser) bypasses
  RLS by default. The testcontainers `test` user is a superuser and bypasses RLS
  natively — the existing integration tests use a dedicated non-privileged
  `app_role` to prove isolation, which is correct but insufficient: FORCE RLS
  is not tested, and the table-owner path is unguarded.
- `tenancy/service.py::resolve_key()` issues a raw ORM join query. Under FORCE
  RLS, this query would return no rows (no tenant context set at bootstrap time).
- `persistence/rls.py::apply_rls_tenant()` and `engine.py::set_rls_tenant()`
  both inline a `uuid.UUID()`-validated value. The validation exists; the test
  proving injection is rejected does not.
- 573 tests / 87.49% coverage / ruff + mypy clean.

### Load-bearing — do NOT touch
- `config/llm_routing.yaml`
- `agents/optimizer.py`, `agents/conversation_manager.py`
- `evals/optimizer/thresholds.py`, `runner.py`
- `llm/` adapters
- `.github/workflows/deploy-*.yml`

## Scope

### In scope
- New Alembic migration: `ALTER TABLE tenants FORCE ROW LEVEL SECURITY` +
  `ALTER TABLE api_keys FORCE ROW LEVEL SECURITY` + a `SECURITY DEFINER`
  function `resolve_api_key_secure(p_key_hash text) RETURNS uuid` that returns
  the matching `tenant_id` (and nothing else). The function's `SET search_path`
  must be pinned to prevent search-path injection.
- Update `tenancy/service.py::resolve_key()` to call the SECURITY DEFINER
  function for the bootstrap lookup, then set the RLS context and query the full
  Tenant object via the normal (RLS-scoped) ORM path.
- `persistence/rls.py` + `engine.py`: add an explicit `_validate_tenant_id()`
  helper that calls `uuid.UUID()`, raises `ValueError` on invalid input, and is
  called at every `SET LOCAL` site. The inline SQL must not be reachable without
  passing through this helper.
- Tests:
  - **Injection-rejection**: `_validate_tenant_id` rejects `"not-a-uuid"`,
    `"'; DROP TABLE tenants; --"`, `"00000000-0000-0000-0000-000000000000'; x"`,
    and empty string — all must raise before producing SQL.
  - **FORCE RLS isolation**: prove tenant A cannot read tenant B's rows on a
    connection that is the table owner (not a superuser). Use `SET ROLE` in
    testcontainers to switch to a non-superuser `app_owner` role that owns the
    tables; assert RLS isolation holds.
  - **SECURITY DEFINER bootstrap**: prove `resolve_key()` returns the correct
    tenant even when the app_role connection has no `app.current_tenant` set
    (i.e., the SECURITY DEFINER bypass works at the function boundary only).

### Out of scope
- Rate-limit enforcement (Phase 3.2-B).
- Second inventory adapter (Phase 3.2-C).
- Cloud SQL provisioning or any prod deploy.
- Any change to `RequestState` schema.

## Tech stack
No new dependencies. Same stack as Phase 3.2-A.

## Architecture changes

```
apps/api/src/travel_agent/
├── persistence/
│   ├── rls.py          # add _validate_tenant_id(); both SET LOCAL sites use it
│   └── engine.py       # set_rls_tenant() calls _validate_tenant_id()
├── tenancy/
│   └── service.py      # resolve_key() → SECURITY DEFINER fn + RLS-scoped follow-up
└── persistence/migrations/versions/
    └── 20260608_b2c3d4e5f6a7_force_rls_security_definer.py   # new migration
```

### SECURITY DEFINER function design

```sql
-- Pinned search_path prevents injection via a malicious schema.
-- Returns only tenant_id (minimum data for the bypass).
-- Owned by the superuser/BYPASSRLS role that creates it.
CREATE FUNCTION resolve_api_key_secure(p_key_hash text)
RETURNS uuid
SECURITY DEFINER
SET search_path = public
LANGUAGE sql
AS $$
    SELECT t.id
    FROM   api_keys k
    JOIN   tenants  t ON t.id = k.tenant_id
    WHERE  k.key_hash    = p_key_hash
      AND  k.is_active   = true
      AND  t.is_active   = true
    LIMIT 1;
$$;
```

Python call flow after this change:
```
resolve_key(raw_key, session)
  1. key_hash = hash_key(raw_key)
  2. tenant_id = await session.scalar(
         text("SELECT resolve_api_key_secure(:kh)"), {"kh": key_hash}
     )                                  # bypasses RLS via SECURITY DEFINER
  3. if tenant_id is None → return None
  4. await set_rls_tenant(session, str(tenant_id))  # set context
  5. tenant = await session.get(Tenant, tenant_id)  # normal RLS-scoped fetch
  6. return tenant
```

### `_validate_tenant_id` helper

```python
# persistence/rls.py
import uuid as _uuid_mod

def _validate_tenant_id(tenant_id: str) -> str:
    """Return the canonical UUID string or raise ValueError.

    Called at every SET LOCAL site so that no injection-shaped value
    can reach SQL. uuid.UUID() accepts only the canonical hex+hyphen form.
    """
    return str(_uuid_mod.UUID(tenant_id))
```

Both `apply_rls_tenant` (rls.py) and `set_rls_tenant` (engine.py) call this
before constructing the SQL string.

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

Tests run against a local/docker Postgres via testcontainers.

## Build order
1. Write the migration: FORCE RLS on both tables + SECURITY DEFINER function.
2. Update `tenancy/service.py::resolve_key()` to use the two-step
   (SECURITY DEFINER → RLS-scoped ORM fetch) pattern.
3. Extract `_validate_tenant_id()` into `persistence/rls.py`; wire both SET
   LOCAL sites through it; verify existing integration tests still pass.
4. Write tests:
   - Injection-rejection unit tests (no DB, pure Python ValueError assertions).
   - FORCE RLS isolation integration test (SET ROLE → app_owner, assert A≠B).
   - SECURITY DEFINER bootstrap integration test (app_role, no ctx set → resolves).
5. Verifier pass. Coverage must be ≥86%.

## Escalation rules
- Ask before any cloud deploy or Cloud SQL provisioning.
- Ask before installing any dependency beyond the existing stack.
- Escalate if `DEMO_API_KEY` backward compat breaks.
- Escalate if any of the 573 existing tests fail.
- Escalate if the SECURITY DEFINER function cannot be created in testcontainers
  without superuser privileges (describe the constraint; do not work around it
  silently).

## Hard rules
- Do NOT deploy to prod or staging.
- Do NOT break `DEMO_API_KEY` auth.
- Do NOT touch load-bearing files.
- Do NOT set `ANTHROPIC_API_KEY`.
- Store API keys hashed only (unchanged from 3.2-A).
- `_validate_tenant_id` must be called at EVERY `SET LOCAL` site — no bypass.

## Budget
- Soft target: 1 CC session.
- Hard cap: stop and escalate after 20 executor invocations.
- Run `/cost` at midpoint and report.

## Success criteria (verify ALL before declaring done)
- `FORCE ROW LEVEL SECURITY` active on `tenants` and `api_keys` (check via
  `pg_tables` or `information_schema` in a test or migration verification).
- `resolve_api_key_secure` SECURITY DEFINER function exists and is callable by
  the app role.
- `resolve_key()` uses the two-step pattern; no direct ORM join bypasses FORCE
  RLS.
- `_validate_tenant_id()` is the sole path to `SET LOCAL`; all injection-rejection
  tests pass (ValueError before SQL).
- FORCE RLS isolation integration test passes (table-owner connection, A≠B).
- SECURITY DEFINER bootstrap integration test passes.
- All 573 existing tests pass (count may grow).
- Coverage ≥86%. ruff + mypy clean.
- No cloud deploy; prod still `00025-gaw`.
