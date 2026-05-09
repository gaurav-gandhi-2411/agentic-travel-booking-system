# ADR-0004: Postgres Row-Level Security for Multi-Tenant Isolation

**Status:** Accepted — 2026-05-09

---

## Context

The system is multi-tenant from v1 (plan.md §8). Every table that stores tenant data
must be isolated: tenant A must never read, write, or be billed for tenant B's data.
This includes:

- `tenants`, `api_keys`, `provider_credentials`, `affiliate_configs`, `rate_limit_configs`,
  `scoring_weights` (tenant configuration)
- `sessions`, `travel_intents`, `request_states` (per-request state)
- `flight_options`, `hotel_options` (cached provider results)
- `booking_audit` (the append-only booking log, 7-year retention)
- `cost_ledger` (per-request cost attribution)

Isolation failures in multi-tenant SaaS are severe: they expose PII (traveler names,
payment references), business-sensitive data (affiliate IDs, pricing strategies), and
breach the trust of the B2B buyer who signed a data isolation contract.

The system uses Neon (managed Postgres) and asyncpg for the connection pool. There is
no query builder ORM (we use raw SQL with asyncpg). Each FastAPI request is handled
by an async worker that serves exactly one tenant per request lifecycle.

The isolation mechanism must:
1. Be enforced at the database layer (not just app layer), so a code bug that forgets
   a `WHERE tenant_id = $1` clause is caught by the database, not visible in production.
2. Work with Neon's serverless architecture and asyncpg connection pooling.
3. Add minimal query latency — the `tenant_id` filter must use an index.
4. Be auditable by enterprise buyers who ask for a data isolation architecture review.

---

## Decision

We use **Postgres Row-Level Security (RLS)** as the primary isolation mechanism, with
app-layer `tenant_id` filtering as a defense-in-depth secondary layer.

### Implementation

**1. All tenant-scoped tables have `tenant_id UUID NOT NULL`.**

Every table that stores data for a specific tenant has a `tenant_id` column indexed for
query performance:

```sql
CREATE INDEX idx_booking_audit_tenant ON booking_audit(tenant_id);
CREATE INDEX idx_cost_ledger_tenant   ON cost_ledger(tenant_id);
-- etc. for all tenant-scoped tables
```

**2. RLS is enabled on every tenant-scoped table.**

```sql
ALTER TABLE booking_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_ledger   ENABLE ROW LEVEL SECURITY;
-- etc.
```

**3. RLS policies use a session-level setting.**

```sql
CREATE POLICY tenant_isolation ON booking_audit
    USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE POLICY tenant_isolation ON cost_ledger
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
-- etc.
```

**4. The connection pool sets `app.tenant_id` at transaction start.**

In `apps/api/src/travel_agent/tenancy/rls.py`:

```python
async def set_tenant_context(conn: asyncpg.Connection, tenant_id: UUID) -> None:
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, true)",  # true = local to transaction
        str(tenant_id),
    )
```

The FastAPI middleware layer (in `apps/api/src/travel_agent/api/middleware/`) extracts
the `tenant_id` from the validated API key (via `tenancy/auth.py`) and calls
`set_tenant_context` on every connection used for that request.

The `true` parameter to `set_config` scopes the setting to the current transaction,
not the entire session. This is critical for connection pooling: the setting is reset
when the transaction ends, so a pooled connection returned to the pool carries no
tenant state that would leak to the next request.

**5. App-layer filtering is still applied.**

Every query in the persistence layer also includes `WHERE tenant_id = $1` explicitly.
The app-layer filter is the fast path (it uses the index with a bound parameter); RLS
is the safety net. This defense-in-depth means a missing app-layer filter returns zero
rows (caught in testing) rather than all tenants' rows.

**6. The `postgres` superuser is not the application user.**

The application connects as a role (`travel_agent_app`) that:
- Has `CONNECT` on the database.
- Has `SELECT, INSERT, UPDATE, DELETE` on tenant-scoped tables.
- Does NOT have `BYPASSRLS` — RLS applies to this role.

A separate `travel_agent_migration` role (used by Alembic only) has schema-level
DDL privileges and is used only during deployments, never during request serving.

### Tables exempt from RLS

- `tenants` — this table is what we key off of. The app reads it by `id` (primary key
  lookup) to resolve an API key. No cross-tenant data is exposed since tenants can only
  look themselves up via their own API key.
- `alembic_version` — schema metadata, no tenant data.

---

## Consequences

**Positive:**
- Database-level enforcement means a bug in `persistence/audit.py` that forgets the
  `WHERE tenant_id = $1` clause returns zero rows for an incorrectly resolved tenant,
  not all tenants' rows. The data breach scenario requires both the app-layer filter AND
  the RLS policy to fail simultaneously.
- Enterprise buyers asking for a data isolation architecture diagram get a concrete,
  auditable answer: "Postgres RLS policies enforce `tenant_id` on every row, applied
  at the database level by a role that cannot bypass RLS."
- RLS policies are version-controlled in Alembic migrations. Every policy change is
  reviewable in git history.
- No additional infrastructure component for isolation — RLS is a native Postgres
  feature available in Neon at every tier.
- The `set_config('app.tenant_id', ..., true)` transaction-local scoping is safe with
  asyncpg connection pooling because the setting resets at transaction end.

**Negative:**
- Every new table requires: (a) a `tenant_id UUID NOT NULL` column, (b) `ENABLE ROW
  LEVEL SECURITY`, (c) a `CREATE POLICY` statement, and (d) an index. This is a 4-step
  checklist that must be enforced in code review. A missing step means the table is
  unprotected.
- RLS adds a small per-row evaluation overhead (comparing `tenant_id` to the session
  setting). At v1 volumes (hundreds of requests/day per tenant), this is negligible.
  At high scale with millions of rows per table, the index design becomes critical.
- The `set_config` call must happen inside a transaction before any query. Queries
  issued outside a transaction (e.g., connection health checks) must not touch
  tenant-scoped tables. The `SELECT 1` health check in `/health` is exempt.
- RLS does not protect against a compromised database superuser or a migration role
  used in the request path. These are operational controls, not RLS controls.
- Testing RLS policies requires creating real Postgres sessions with role-switching,
  not just mocking the query layer. `testcontainers[postgres]` is used for this.

**Neutral:**
- RLS is transparent to the query author. A developer writing `SELECT * FROM booking_audit`
  in an application context will see only their tenant's rows. This is the intended
  behavior, but it means local development queries run against a `psql` session without
  `app.tenant_id` set will return zero rows by default. Developers need to `SET
  app.tenant_id = 'uuid-here'` in psql to inspect data.

---

## Alternatives Considered

### Alternative 1: App-layer filtering only

Every query includes `WHERE tenant_id = $1`. No RLS. The app layer is the sole isolation
mechanism.

**Rejected because:**
- A single code path that forgets the tenant filter exposes all tenants' data. At the
  complexity of this codebase (six agents, multiple persistence modules, complex query
  paths), ensuring 100% filter coverage by code review alone is not acceptable.
- Enterprise buyers in regulated industries (financial services, healthcare-adjacent
  travel) explicitly ask for database-level isolation. "We filter in the application"
  is not a satisfactory answer during procurement due diligence.
- Offers no defense against internal tooling bugs (e.g., a one-off analytics query
  that forgets the tenant filter).

### Alternative 2: Schema-per-tenant

Each tenant gets their own Postgres schema: `tenant_abc.booking_audit`,
`tenant_def.booking_audit`, etc. Alembic runs migrations per schema.

**Rejected because:**
- Alembic migration management across N schemas is complex. Each deployment runs the
  migration N times. Adding a new tenant requires creating a schema and running all
  migrations.
- Neon's free tier has per-database storage limits, not per-schema. Schema-per-tenant
  does not improve Neon cost or resource isolation.
- asyncpg connection pooling with schema-per-tenant requires per-tenant connection pools
  (since `search_path` must be set per-tenant), eliminating connection reuse across
  tenants.
- At 10+ tenants, managing schema evolution (e.g., a column rename) becomes a
  coordination problem: apply to all schemas atomically or risk schema drift.

### Alternative 3: Database-per-tenant

Each tenant gets a separate Neon database (or Neon branch acting as a database).

**Rejected because:**
- Neon free tier allows 1 database. Neon paid tiers allow multiple databases but at
  $19–$69/month per project. At 10 tenants, this is $190–$690/month before we have
  meaningful revenue.
- Connection pool management becomes per-database, multiplying pool overhead.
- Cross-tenant analytics (e.g., aggregate performance dashboards for the system operator)
  require federation across multiple databases.
- Neon's branching feature is designed for dev/staging isolation (branch = staging env),
  not tenant isolation — using it that way conflates two different concerns.

---

*Referenced plan.md sections: §7.4, §8.1, §8.2, §8.3, §9, §14*
