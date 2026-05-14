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

---

## Unit 2 — Phase A: Remaining LLM Adapters (2026-05-14)

- **`_openai_compat.py` system-message path (line 31)**: `if system: api_messages.append(...)` is
  untested. Add a cassette with a system message once PlannerAgent system prompts land in Phase C.
  Phase D prereq.

- **`_openai_compat.py` error path (lines 47-48)**: `except openai.APIError` branch not covered.
  Add a test that monkeypatches `client.chat.completions.create` to raise `openai.APIError`.
  Phase D prereq.

---

## Unit 3 — Phase B: Synthetic Provider + Coordinator Skeleton (2026-05-14)

- **`OptimizerAgent` stub**: pass-through for Phase B. Real scoring (value/experience Pareto
  extraction) lands in Phase D. See `agents/optimizer.py`.

- **`PlannerAgent` stub**: raises `NotImplementedError`. LLM-powered intent parsing lands in
  Phase C. Coordinator currently requires `state.intent` to be pre-populated.

- **`BookingAgent` / `ConversationManagerAgent` stubs**: Phase E/F work.

- **Destination city mapping** in `coordinator/constants.py` (`IATA_TO_CITY`): only covers
  CDG/NRT/DPS. Extend before adding new routes in Phase B+.

---

## Unit 4 — Phase C: PlannerAgent + Aviasales + Hunter Agents (2026-05-14)

- **`EXTRACT_FLIGHT_OPTIONS` / `EXTRACT_HOTEL_OPTIONS` tools unused**: These ToolDefinition
  objects in `agents/tools.py` exist as schema contracts but FlightHunterAgent /
  HotelHunterAgent do not currently call the LLM to normalize provider output. If
  provider data quality varies (real Aviasales vs hypothetical Duffel), an LLM normalization
  pass using these tools could improve consistency. Phase D consideration.

- **AviasalesAdapter `return_at` parameter**: `get_flights()` accepts `return_at` but the
  current FlightHunterAgent does not pass it. For round-trip searches, passing the return
  date would narrow results. Wire when OptimizerAgent starts scoring round-trip packages.

- **`_openai_compat.py` system-message path (line 31)**: PlannerAgent system prompts are now
  live — add a cassette with a non-empty system message for the Ollama/OpenRouter/Groq/vLLM
  adapters to close this gap.

- **Aviasales `limit` parameter**: hard-coded to 30. If the coordinator searches many windows,
  per-window limits may be worth tuning. Config value in `coordinator.yaml` would allow easy
  adjustment.

- **Integration test uses SyntheticProvider only**: `test_coordinator_pipeline.py` wires
  `FlightHunterAgent()` without an AviasalesAdapter. A VCR-backed integration test that
  injects a real AviasalesAdapter with cassette replay would increase confidence in the
  full Aviasales path end-to-end.

---

## Unit 5D — Demo Bug Fixes (2026-05-15)

- **Free-tier Groq routing profile**: Add a `groq` profile to `config/llm_routing.yaml`
  for zero-cost CI evaluation using Groq's hosted Llama endpoints (free tier: ~50 req/day).
  Would allow nightly eval runs without consuming Anthropic credits. Groq base_url and
  model IDs already supported by the OpenAI-compat adapter.

- **Third archetype consideration**: The Pareto frontier consistently surfaces 3–5 options.
  Currently only 2 archetypes are shown (best-value, best-experience). A third archetype
  "best-balance" (nearest to the Pareto knee — minimising the distance to the ideal point)
  could improve the demo story. Requires UI update (3-card layout) and OptimizerAgent change.

- **Aviasales API ceiling**: The `prices_for_dates` endpoint is a cached price calendar,
  not GDS inventory. Indian international routes yield 6–16 results per month, not 30+.
  When a real-time GDS connection (Duffel, Amadeus) is added in Phase E, remove the
  month-granularity workaround in FlightHunterAgent and replace with per-window calls.

- **Demo chip routes are Delhi-heavy**: BOM and BLR routes lack the non-stop/1-stop price
  inversion needed for distinct archetypes with the current API data. Revisit when live
  inventory data (Duffel) replaces the Aviasales price calendar.
