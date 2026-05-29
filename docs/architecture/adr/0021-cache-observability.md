# ADR-0021 — Cache Backend Observability (Phase 2D.1)

## Context

During Phase 2C.4 staging verification, a cross-instance cache miss was observed between
a `/search` request and a subsequent `/refine` call (Issue #31). Diagnosis was impossible
because `_make_cache()` in `api/cache.py` used `contextlib.suppress(Exception)` to silently
fall back from Redis to the in-memory backend. No log lines were emitted for:

- Which backend was selected at startup (redis vs in_memory)
- Why the fallback fired (error class and message)
- Whether individual put/get operations on Redis succeeded

Cloud Run autoscaling means multiple revisions can serve requests concurrently. Without
per-revision attribution on cache logs, it is impossible to determine whether a cache miss
is cross-instance (expected on in-memory), within-instance (TTL expiry or bug), or a Redis
connectivity issue.

## Decision

Add four structured log events to the cache subsystem:

1. **`cache_backend_selected`** — emitted by `_make_cache()` on every process start.
   Fields: `backend` ("redis" | "in_memory"), `revision`.

2. **`cache_init_fallback`** — emitted inside the try/except block when `RedisSearchCache`
   init raises. Fields: `error_class`, `error_message`, `revision`.

3. **`search_cache_put_success`** — emitted by `RedisSearchCache.put()` on successful write.
   Fields: `request_id`, `revision`.

4. **`search_cache_get_result`** — emitted by `RedisSearchCache.get()` for both hits and
   misses (not emitted on errors, which already log `search_cache_failure`).
   Fields: `request_id`, `hit` (bool), `revision`.

Every event includes `revision` = `K_REVISION` env var (Cloud Run revision name),
falling back to `socket.gethostname()` for local/non-Cloud-Run environments.

The `contextlib.suppress(Exception)` pattern is replaced with an explicit try/except
that logs before allowing execution to fall through to the in-memory backend.

## Consequences

- All cache backend selection decisions are now visible in Cloud Run structured logs.
- Future cross-instance cache misses can be diagnosed by filtering logs for
  `cache_backend_selected` with `backend="in_memory"` — indicates Redis init failed.
- No behavior change. The logging is additive; the fallback logic is identical.
- `search_cache_failure` (existing error event) is retained unchanged.

## Alternatives Considered

- **Add logging only to `cache_init_fallback`:** Rejected. Without `cache_backend_selected`
  on the happy path, we cannot confirm Redis is actually being used after a clean deploy.

- **Add Prometheus metrics instead of log events:** Out of scope for this iteration.
  Structured log events are sufficient for the diagnosis use case and require no new
  infrastructure. Prometheus metrics for cache hit rate are a Phase 2D+ item.

- **Keep `contextlib.suppress` and wrap it:** Rejected. Wrapping suppress to log the
  suppressed exception is more complex than replacing it with an explicit try/except.

## References

- Issue #31 — Cache backend selection is silently observable
- Phase 2C.4 staging incident: cross-instance cache miss at 2026-05-27T19:35Z
