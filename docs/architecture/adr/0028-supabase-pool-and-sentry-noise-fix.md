# ADR-0028 — Supabase Transaction Pooler for Runtime Connections; Sentry Fallback-Noise Filter

**Date:** 2026-07-04
**Status:** Accepted
**Phase:** Post-fallback-chain hardening

---

## Context

The ADR-0027 canary smoke test (forced-outage verification of the LLM fallback chain)
surfaced two issues unrelated to the fallback chain itself, both from the concurrent
burst used to force Groq's rate limit:

**(a) DB connection pool exhaustion.** `asyncpg.exceptions.InternalServerError:
(EMAXCONNSESSION) max clients reached in session mode - max clients are limited to
pool_size: 15` — one occurrence, at the exact start of the burst. Root cause:
`persistence/engine.py`'s `create_async_engine()` had no explicit `pool_size`/
`max_overflow`, so SQLAlchemy's defaults applied: `pool_size=5, max_overflow=10 => 15`
— which happens to exactly match Supabase's session-pooler ceiling on this project
(shared with a co-tenant app). A single Cloud Run instance's own default pool could
therefore exhaust the *entire* shared pooler alone, with zero headroom left for the
co-tenant, under nothing more than an ordinary concurrent burst. `TenantAuthMiddleware`
(which opens a DB session on every `/search`/`/refine`/`/book`/`/cancel` request purely
to resolve the API key) had no retry/backoff around this, so a transient pool-full
condition surfaced as a raw unhandled 500 to the client.

**(b) Sentry noise from the fallback chain's own success cases.** `sentry-sdk` auto-
enables an `OpenAIIntegration` for any process with `openai` installed (all our
OpenAI-compatible adapters — Groq, OpenRouter, NIM, Ollama, VLLM — use `openai.AsyncOpenAI`
under the hood). That integration wraps every `chat.completions.create()` call and
unconditionally calls `capture_exception()` on any exception it raises, then re-raises
it — independent of whether `FallbackLLMClient` goes on to recover via the next hop a
moment later. Sentry showed `RateLimitError` (64 events) and `LLMError` (21 events) after
the canary smoke; cross-referencing Cloud Run structured logs and the smoke test's own
saved response files confirmed **100% of both counts** were self-inflicted by the smoke
test itself (62/64 matched `llm_fallback_attempt_failed{retryable=true}` events; all 21
matched `"All 2 LLM providers exhausted"` in the saved responses) — not a functional bug
(the fallback demonstrably worked, per ADR-0027's structlog evidence), but a real
observability-quality problem: every future genuine Groq rate-limit hit would generate a
Sentry error indistinguishable from an unhandled failure, even when the fallback silently
and successfully absorbed it — a clear path to alert fatigue.

---

## Decision

### (a) Move runtime DB connections to the Supabase transaction pooler (port 6543)

**Chain of reasoning, in order:**

1. **The math rules out tuning the session pooler.** Supabase's session-pooler ceiling
   here is a hard 15 clients, shared with a co-tenant. Cloud Run's `--max-instances=20`
   means the fleet-wide worst case is `max_instances x (pool_size+max_overflow)` — with
   20 instances, *any* per-instance pool `> 0` already risks exceeding 15 combined, before
   the co-tenant even factors in. There is no `pool_size` value that gives this app real
   burst headroom on session mode without capping `max_instances` down to single digits —
   which would handicap Cloud Run's scaling for the (unrelated) LLM-heavy `/search`/
   `/refine` endpoints to fix a database problem.
2. **Transaction pooling is the architecturally correct fit** for "many autoscaled,
   short-lived clients doing quick DB hits" — it multiplexes many client connections over
   few backend ones (checked out per-transaction, not per-connection-lifetime), instead of
   session mode's one-backend-connection-per-client model. Supabase's free-tier Supavisor
   ceiling for this is **200 max client connections** (Supabase compute-and-disk docs,
   Nano/free tier) — a different, much larger number from the session-mode "pool_size: 15"
   that appeared in the error.
3. **Re-checked whether the original session-pooler decision (CURRENT_STATE.md: "transaction-
   mode 6543 is unsafe — a non-LOCAL `SET search_path` can be lost between statements")
   still applies to this specific code path** — it doesn't, for two reasons:
   - `resolve_api_key_secure` already has `SET search_path = dealhunter` bound into the
     *function definition itself* (migration `b2c3d4e5f6a7`) — Postgres applies a
     function's own `SET` clause on every invocation regardless of the calling
     connection's search_path. Already 100% transaction-pooling-safe.
   - `session.get(Tenant, tenant_id)` (step 2 of `resolve_key()`) was the one place still
     relying on connection-level `search_path` (the ORM models had no explicit `schema=`).
     **Fixed:** `Tenant`/`ApiKey` now declare `__table_args__ = {"schema": DB_SCHEMA}` —
     SQLAlchemy emits fully-qualified `dealhunter.tenants`/`dealhunter.api_keys` SQL,
     independent of the connection's ambient search_path entirely. This is the one
     load-bearing safety change and was re-verified live (see Verification below), not
     just unit-tested.
   - Migrations are untouched — Alembic (`env.py`) still reads `DATABASE_URL` directly and
     stays on the session pooler unconditionally; DDL/multi-statement migration safety was
     never in question for this fix.

**Pool math** (documented in `persistence/engine.py` as code comments, not just here):
```
200  (free-tier Supavisor client ceiling, confirmed via Supabase docs)
/ 2  (deliberate 50/50 split with the co-tenant — their usage is unknown/uncontracted)
= 100  our worst-case fleet budget
/ 20  (Cloud Run --max-instances, deploy-prod.yml)
= 5 connections/instance at full scale-out
```
`pool_size=3, max_overflow=2` (=5/instance, explicit) x 20 max instances = 100 fleet-wide
worst case = exactly half the 200-client ceiling. `pool_timeout=5s` (also explicit) so a
starved request fails fast into the retry-then-503 layer below, rather than blocking for
SQLAlchemy's 30s default.

**Wiring:** `get_engine()` prefers a new `DATABASE_URL_RUNTIME` env var (the transaction-
pooler connection string), falling back to `DATABASE_URL` (session pooler) if the new
secret isn't provisioned — safe to deploy in either order. **No port-derivation logic
exists in the runtime code** — `DATABASE_URL_RUNTIME` must be an explicit, separately-
provisioned secret (same role/credentials as `DATABASE_URL`, just port 6543 instead of
5432). This was a deliberate choice, not an oversight: the whole premise of this fix is
"explicit configuration, not guessed defaults" (the original bug *was* an unexamined
default), so baking a port-swap assumption into production code would undercut that
principle for the sake of one less manual step.

### (a), part 2 — graceful degradation instead of an unhandled 500

`TenantAuthMiddleware` now retries `resolve_key()` up to 2 times (short backoff: 100ms,
300ms) on `OperationalError`/`TimeoutError` specifically — transient pool contention
typically clears within milliseconds as other requests release their connections. Any
*other* exception (a genuine bug, not a connectivity blip) propagates immediately; retrying
it would just fail the same way three times instead of once. On retry exhaustion, the
middleware returns a structured `503 {"detail": "Service temporarily busy, please retry."}`
instead of letting the raw connection exception surface as a generic 500.

### (b) Sentry `before_send` tag-filter for recovered fallback-hop noise

Considered disabling `OpenAIIntegration` outright — rejected: it also provides latency/
performance spans, and more importantly, routing profiles *without* a fallback chain
(`demo-gpt-oss-120b`, `demo-deepseek-v4`, `free`, ...) have no other Sentry error-capture
path for their LLM calls at all, since `search.py`/`refine.py` catch and convert every
exception to an SSE `error` event before it ever reaches FastAPI's own auto-capture.
Disabling the integration wholesale would blind Sentry to real failures on every
non-fallback profile — a regression, not a cleanup.

**Instead:** `FallbackLLMClient.chat()` wraps each hop attempt in `sentry_sdk.new_scope()`,
tagging it `llm_fallback_managed=true`. `observability/sentry.py`'s existing `before_send`
hook (already used for credential scrubbing) drops the event when **both**: (1) it carries
that tag, and (2) the exception class is one of the four retryable provider types
(`RateLimitError`, `APIConnectionError`, `APITimeoutError`, `InternalServerError`).
Everything else passes through unchanged:
- A **non-retryable** exception inside a fallback-managed call (e.g. a genuine bad API
  key) still reaches Sentry — nothing else captures that case, since `FallbackLLMClient`
  only calls Sentry itself on success or full exhaustion, never on an immediate
  non-retryable bail-out.
- **Full-chain exhaustion** still reaches Sentry — via `FallbackLLMClient`'s own explicit
  `capture_exception`, which carries richer context (from/to provider, hop index) than the
  raw auto-capture ever would; the raw per-hop duplicate for the terminal hop is dropped
  too, since the explicit capture already fully covers it.
- **Non-fallback-managed profiles** are completely unaffected — no tag, no filtering.
- **"Fallback served" warnings** are separate `capture_message` calls, outside the tagged
  scope, untouched by this filter either way.

---

## Verification

**(a) — live, against the real Supabase instance, not simulated:**
`scripts/verify_transaction_pooler_isolation.py` connected the real least-privilege
`dealhunter_app` role through the transaction pooler (port 6543, derived from the existing
session-pooler secret for this one-time test only — production uses the explicit
`DATABASE_URL_RUNTIME` secret), created two temporary test tenants, and re-ran the full
cross-tenant isolation battery: `resolve_key()` end-to-end (valid/invalid key), SELECT
(own row visible, other tenant's row invisible, no-context default-deny), UPDATE
cross-tenant (0 rows), DELETE cross-tenant (0 rows), INSERT cross-tenant (rejected by
`WITH CHECK`), and bootstrap-GUC exposure (reveals exactly the presented row). **12/12
PASS.** Confirmed zero residual rows after cleanup. The startup guard
(`assert_runtime_role_unprivileged`) was not separately live-tested here because it runs
the identical `pg_roles` query against the identical role/connection type the isolation
script already exercised (`_role_flags()` confirmed non-superuser, non-BYPASSRLS,
`current_user=dealhunter_app`) — same evidence, not a separate code path.

**(a), part 2 and (b) — unit-tested** (retry-then-503 behavior, non-transient errors
skip the retry, before_send tag+exception-class filtering, scope-tag non-leakage). Full
live-dashboard confirmation of (b) — that a recovered 429 does NOT appear in Sentry while
full exhaustion DOES — is deferred to the next canary smoke test (forced concurrency,
forced recovered 429, forced full exhaustion), the same live-forced-outage discipline
ADR-0027 established.

### Addendum — DuplicatePreparedStatementError under real concurrency (found post-canary)

The 12/12 PASS above was real but incomplete: every check in that battery ran one session
at a time. The first genuinely concurrent canary smoke (a ~24-request burst, part 1 of the
ADR-0027-style forced-outage discipline) surfaced `asyncpg.exceptions.
DuplicatePreparedStatementError` on 11/24 requests — a raw 500, not the structured 503 this
ADR's retry logic was designed to produce (correctly so: `ProgrammingError` isn't transient,
and the retry layer was right not to catch it).

**Root cause**, confirmed against the installed SQLAlchemy dialect source
(`sqlalchemy/dialects/postgresql/asyncpg.py`), not guessed: the original fix set
`connect_args["statement_cache_size"] = 0` — the raw asyncpg-only parameter name, silently
a no-op when passed through SQLAlchemy's `connect_args` (SQLAlchemy's own asyncpg dialect
recognizes `prepared_statement_cache_size` instead). With the real parameter still unset,
SQLAlchemy's asyncpg adapter called `connection.prepare(operation, name=self.
_prepared_statement_name_func())` on every execution using its **default name function**,
which returns `None` and lets asyncpg apply its own sequential per-connection-object naming
(`__asyncpg_stmt_1__`, `_2__`, ...). Two different concurrent client connections can
independently reach the same sequential name and collide when Supavisor's transaction-mode
multiplexing routes both onto the same backend at the same moment — exactly SQLAlchemy's
own documented "Prepared Statement Name with PGBouncer" failure mode.

**First fix (PR #74):** `prepared_statement_cache_size=0` **and**
`prepared_statement_name_func=lambda: f"__asyncpg_{uuid4()}__"` together
(`persistence/engine.py::_pooler_connect_args()`), matching SQLAlchemy's own prescribed
remedy. This is correct and sufficient for every ORM/Core query executed through a
`Session` — but it was **not the whole bug**.

**Re-verified against the fixed canary, still not clean:** redeploying and re-running the
identical 24-request burst still produced 3/24 raw errors —
`InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_N__" does not exist` and
`DuplicatePreparedStatementError: prepared statement "__asyncpg_stmt_1__" already exists` —
the same failure signature, on a smaller scale. Confirmed via Cloud Run structured logs, not
inferred: this was a genuinely separate code path from the one PR #74 fixed.

**Second root cause:** `get_engine()` sets `pool_pre_ping=True` (deliberately, to reject
dead connections before use). Its liveness check — `do_ping()` → `_async_ping()` →
`self._connection.fetchrow(";")` (`sqlalchemy/dialects/postgresql/asyncpg.py`) — calls
`fetchrow()` **directly on the underlying asyncpg connection**, bypassing
`AsyncAdapt_asyncpg_connection._prepare()` (and therefore `prepared_statement_name_func`)
entirely. `fetchrow()` goes through asyncpg's own internal cache (`asyncpg/connection.py`,
`Connection._get_statement()`), controlled by a *different*, raw asyncpg parameter:
`statement_cache_size` (default 100 — NOT `prepared_statement_cache_size`, which only the
SQLAlchemy dialect recognizes). Confirmed by reading the SQLAlchemy dialect's `connect()`
wrapper: it pops exactly `prepared_statement_cache_size`/`prepared_statement_name_func`/
`async_fallback`/`async_creator_fn` from `connect_args` and forwards everything else
verbatim into `asyncpg.connect(**kw)` — so `statement_cache_size` reaches raw asyncpg
untouched, left at its default, still auto-naming statements via its own sequential
`_get_unique_id('stmt')` counter for this one code path. Same collision, different layer.

**Complete fix:** add raw `statement_cache_size=0` alongside the two SQLAlchemy-level
settings. With asyncpg's own cache disabled, `_get_statement()` falls through to an
**anonymous** (empty-name) prepared statement for `do_ping()`'s `fetchrow()` call —
Postgres's protocol allows an unnamed statement to be silently replaced on each use, so
there is no persistent name left to collide, and no stale-plan risk if Supavisor swaps the
backing backend connection between calls. All three settings are additive, not competing:
SQLAlchemy's own query-execution path always passes an explicit `name=`, so it never
touches asyncpg's internal cache/naming logic regardless of `statement_cache_size` —
disabling it only changes behavior for the one path (`pool_pre_ping`) that calls asyncpg
directly.

`NullPool` (SQLAlchemy's other documented mitigation, paired with PgBouncer-side `DISCARD`)
was deliberately not adopted, to preserve the explicit `pool_size`/`max_overflow` budget
this ADR exists to enforce; `pool_recycle=300` was added instead to bound how long any one
connection's prepared statements can accumulate.

**Process gap, fixed:** `scripts/verify_transaction_pooler_isolation.py` had the identical
wrong parameter name (it was written by copying the same mistaken pattern) and ran fully
sequentially, so it could not have caught either collision. It now imports
`_pooler_connect_args()` directly from `persistence/engine.py` (instead of reimplementing
it) and includes a 24-request concurrent burst as a permanent verification step — a future
change to pooler connect_args cannot regress this silently again, and inherits both fixes
automatically since it calls the shared helper rather than its own copy.

## Consequences

**Positive:**
- The pool-exhaustion bug (a real capacity gap, not just a smoke-test artifact) is fixed
  with a documented, deliberate, worst-case-safe budget instead of an unexamined default.
- A transient DB hiccup now degrades gracefully (retry, then a clean 503) instead of a raw
  unhandled 500.
- Sentry's fallback-related signal is now trustworthy: a served fallback shows up as
  exactly one informative warning, not a spurious duplicate error; full exhaustion still
  reaches Sentry with better context than before.

**Negative / accepted:**
- Requires one additional secret (`DATABASE_URL_RUNTIME`) to be provisioned before the fix
  takes effect; until then, `get_engine()` safely falls back to the session pooler
  (unchanged behavior, not a regression, just not yet the fix).
- The exact Supavisor transaction-mode client ceiling for the free tier is not independently
  re-verified beyond the documented 200 figure — if Supabase's actual enforcement differs
  from docs, the 50/50 co-tenant split is a documented assumption, not a measured one.

## Alternatives considered

**Cap `--max-instances` down instead of switching pooler mode.** Rejected — the math shows
no `max_instances` value compatible with real headroom on a 15-client session-pooler
ceiling without also capping unrelated LLM-endpoint scaling capacity.

**Auto-derive the transaction-pooler URL from `DATABASE_URL` by port substitution in
`get_engine()`.** Rejected for production code (used only in the one-off verification
script) — see Decision above; keeps the "explicit configuration" principle consistent with
why this whole fix exists.

**Disable Sentry's `OpenAIIntegration` entirely.** Rejected — see Decision above; would
blind Sentry to real failures on every routing profile without a fallback chain.
