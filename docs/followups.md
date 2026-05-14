# Deferred Followups

Items noticed during migration phases but out of scope for the current phase.
Each item includes the phase where it was noticed and a brief description.

---

## Unit 1 — Quick Wins Batch (2026-05-14)

- **Q5 waitlist wiring**: `apps/web/components/waitlist-form.tsx` submits to nowhere. Wire to a
  Resend webhook or Vercel KV write. Requires owner to provide API keys. Low priority until
  demo traffic warrants it.

- **structlog dev mode**: `main.py` uses `JSONRenderer` unconditionally. In local dev,
  `ConsoleRenderer` with colors is much more readable. Add `APP_ENV`-based renderer selection
  when the observability phase (Phase G) lands.

- **`lru_cache` on `_load_yaml`**: Currently caches forever per process. In tests that reload
  modules (e.g. lifespan guard tests), `cache_clear()` is called explicitly. Consider a
  short TTL cache (60s) for hot-reload scenarios in development.

- **Anthropic SDK version pin**: `anthropic>=0.40.0` allows major version drift. Consider
  pinning to a minor range once Phase A adapter tests are stable against recorded fixtures.

- **`mypy` strict on `coordinator/state.py`**: The `dict[str, Any]` in `raw` fields of
  `FlightOption` / `HotelOption` is a type escape hatch. Replace with typed provider-specific
  raw models when provider adapters land in Phase B.

- **`test_lifespan_guard_eval_with_key_passes`**: Uses `importlib.reload()` which is fragile
  across test isolation. Refactor when Phase E wires a proper settings object (pydantic-settings)
  so env vars are read at settings instantiation, not at module import.

- **Dockerfile multi-stage**: Current Dockerfile is single-stage. Add a build stage and a
  slim runtime stage in Phase G when the image size starts mattering for Cloud Run cold start.
