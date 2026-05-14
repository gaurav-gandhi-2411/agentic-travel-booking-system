# Migration Log

Record of changes made during the Phase 4 migration plan execution.
Format: date | unit | summary | files touched | test delta | deferred followups.

---

## 2026-05-14 | Unit 1 — Quick Wins Batch

**Summary**
- Q5 (waitlist wiring): SKIPPED — requires Resend/Vercel KV credentials. Flagged for owner.
- Q4: Raised pytest `fail_under` from 0 → 80.
- Q9: Added `RequestIDMiddleware` — generates/echoes `X-Request-ID`, binds to structlog contextvars.
- Q8: Added FastAPI lifespan startup guard — fails fast if `LLM_ROUTING_PROFILE=eval` and `ANTHROPIC_API_KEY` unset.
- Q3: Updated `llm_routing.yaml` — verified model IDs (`claude-sonnet-4-6`, `claude-haiku-4-5-20251001`), added `prod` profile for B2B tenants with own API keys.
- Q7: Added `eval-quick` CI job placeholder to `ci.yml` — dry-run until Phase C agents land.
- Q10: Replaced hardcoded routing table in `routing.py` with YAML loading (`pyyaml>=6.0` dep added); fixed Dockerfile to `COPY config/ config/`; added `LLM_ROUTING_CONFIG_PATH` env override.
- Q1: Extended `base.py` with `ToolDefinition`, `ToolCall` dataclasses; updated `LLMResponse` to include `tool_calls`; added `tools` param to `LLMClient` Protocol.
- Q6: Created `coordinator/state.py` — full `RequestState` Pydantic model tree (`TravelIntent`, `FlightOption`, `HotelOption`, `Window`, `Package`, `BookingStatus`, `CallBudget`).
- Q2: Implemented `AnthropicAdapter.chat()` — real Anthropic SDK calls, tool_use, prompt caching opt-in via `cache_system_prompt` kwarg.

**Files touched:** 15
- `apps/api/pyproject.toml` (modified)
- `apps/api/Dockerfile` (modified)
- `apps/api/config/llm_routing.yaml` (modified)
- `apps/api/src/travel_agent/api/main.py` (modified)
- `apps/api/src/travel_agent/api/middleware/request_id.py` (new)
- `apps/api/src/travel_agent/llm/base.py` (modified)
- `apps/api/src/travel_agent/llm/anthropic.py` (modified)
- `apps/api/src/travel_agent/llm/routing.py` (modified)
- `apps/api/src/travel_agent/llm/__init__.py` (modified)
- `apps/api/src/travel_agent/coordinator/state.py` (new)
- `apps/api/tests/unit/llm/test_protocol.py` (modified)
- `apps/api/tests/unit/llm/test_routing.py` (modified)
- `apps/api/tests/unit/test_main.py` (new)
- `apps/api/tests/unit/coordinator/test_state.py` (new)
- `.github/workflows/ci.yml` (modified)
- `apps/api/.env.example` (modified)

**Test delta:** +2 test files, +~50 new test cases (protocol dataclasses, state models, main endpoint, lifespan guard, routing YAML loading)

**Deferred followups:** see docs/followups.md

---

## 2026-05-14 | Unit 2 — Phase A: Remaining LLM Adapters

**Summary**
- Extracted shared tool format translation into `llm/_tool_translation.py`:
  `to_anthropic_tools`, `parse_anthropic_tool_calls` (content blocks),
  `to_openai_tools`, `parse_openai_tool_calls` (message.tool_calls).
- Extracted shared OpenAI-compatible chat loop into `llm/_openai_compat.py`
  (`openai_compat_chat`) — used by all four non-Anthropic adapters to avoid
  30-line duplication across Ollama/OpenRouter/Groq/vLLM.
- Refactored `AnthropicAdapter` to use translation helpers; removed inline
  dict comprehension and for-loop.
- Implemented `OllamaAdapter` — `http://localhost:11434/v1`, no API key required.
- Implemented `OpenRouterAdapter` — `https://openrouter.ai/api/v1`, requires `OPENROUTER_API_KEY`.
- Implemented `GroqAdapter` — `https://api.groq.com/openai/v1`, requires `GROQ_API_KEY`.
- Implemented `VLLMAdapter` — configurable base URL, `VLLM_API_KEY` defaults to `"EMPTY"`.
- Added `openai>=1.60.0` to main dependencies.
- VCR cassette infrastructure: `tests/fixtures/cassettes/{adapter}/chat.yaml` (5 cassettes),
  `.gitignore` blocking `*secret*` files, `record_mode="none"` in CI.
- Tests: +24 new test cases (18 tool translation unit tests + 7 adapter cassette tests).
  Updated Protocol tests to monkeypatch env vars for key-guarded adapters.

**Files touched:** 15
- `apps/api/src/travel_agent/llm/_tool_translation.py` (new)
- `apps/api/src/travel_agent/llm/_openai_compat.py` (new)
- `apps/api/src/travel_agent/llm/anthropic.py` (refactored)
- `apps/api/src/travel_agent/llm/ollama.py` (implemented)
- `apps/api/src/travel_agent/llm/openrouter.py` (implemented)
- `apps/api/src/travel_agent/llm/groq.py` (implemented)
- `apps/api/src/travel_agent/llm/vllm.py` (implemented)
- `apps/api/pyproject.toml` (openai dep, A002 test ignore)
- `apps/api/.env.example` (OPENROUTER_API_KEY, GROQ_API_KEY, OLLAMA_BASE_URL, VLLM_BASE_URL)
- `apps/api/tests/fixtures/cassettes/.gitignore` (new)
- `apps/api/tests/fixtures/cassettes/{anthropic,ollama,openrouter,groq,vllm}/chat.yaml` (5 new)
- `apps/api/tests/unit/llm/test_tool_translation.py` (new)
- `apps/api/tests/unit/llm/test_adapters.py` (new)
- `apps/api/tests/unit/llm/test_protocol.py` (updated)

**Test delta:** +2 test files, +24 new test cases
**Coverage:** 85% → 93% (74 tests, all passing)

**Deferred followups:** see docs/followups.md

---

## 2026-05-14 | Unit 3 — Hotfix: _openai_compat tools path

**Summary**
Covered the tool-call branch in `llm/_openai_compat.py` (lines 41-42) that was
untested after Unit 2.  One VCR cassette added per OpenAI-compatible adapter.

**Files touched:** 6
- `apps/api/tests/fixtures/cassettes/{ollama,openrouter,groq,vllm}/chat_tool_call.yaml` (4 new)
- `apps/api/tests/unit/llm/test_adapters.py` (4 new tests)
- `docs/followups.md` (Unit 3 deferred items)

**Test delta:** +4 test cases (tools path for Ollama/OpenRouter/Groq/vLLM)
**Coverage:** 93.40% (78 tests, all passing)

---

## 2026-05-14 | Unit 3 — Phase B: Synthetic Provider + Coordinator Skeleton

**Summary**
Full Phase B implementation: coordinator config, synthetic data layer, agent
stubs, coordinator skeleton, and ADR-0013 statistical property tests.

Key design decisions:
- `config/coordinator.yaml` is the single source of truth for call_budget, window
  search, and destination city mapping.  `coordinator/constants.py` loads it once
  via `lru_cache` and exports typed module-level constants; `CallBudget` defaults
  now reference these constants (not hardcoded literals).
- Synthetic flight data: 30 templates (10 per route: BOM-CDG, BOM-NRT, BOM-DPS),
  bimodal LCC/premium price clusters with >=15k INR gap zone on every route.
  Real IATA codes, real airline codes, realistic durations.
- Synthetic hotel data: 20 templates (Paris 8, Tokyo 7, Bali 5).  Star distribution
  skewed 3-star-heavy (10/20).  Three anomalous hotels: Budget Palace Montmartre
  (overpriced 3-star), Le Petit Bijou (underpriced 4-star at INR 4,200 -- cheaper
  than most 3-star), Tokyo Central Hostel Business (review_score 6.2, Shinjuku
  location).
- Coordinator uses asyncio.gather for parallel FlightHunterAgent + HotelHunterAgent;
  each agent receives state.model_copy(deep=True) to prevent mutation races; results
  merged back into a fresh copy.
- ADR-0013 tests cannot skip: bimodal gap, cluster counts, star skew, all three weird
  hotels, determinism, different windows produce distinct IDs.

**Files touched:** 17
- `apps/api/config/coordinator.yaml` (new)
- `apps/api/src/travel_agent/coordinator/constants.py` (new)
- `apps/api/src/travel_agent/coordinator/state.py` (CallBudget defaults from constants)
- `apps/api/src/travel_agent/coordinator/coordinator.py` (new)
- `apps/api/src/travel_agent/providers/data/flights.json` (new -- 30 templates)
- `apps/api/src/travel_agent/providers/data/hotels.json` (new -- 20 templates)
- `apps/api/src/travel_agent/providers/synthetic.py` (new)
- `apps/api/src/travel_agent/agents/base.py` (new -- Agent Protocol)
- `apps/api/src/travel_agent/agents/{planner,flight_hunter,hotel_hunter,optimizer,booking,conversation}.py` (new)
- `apps/api/tests/unit/providers/__init__.py` (new)
- `apps/api/tests/unit/providers/test_synthetic.py` (new -- ADR-0013 tests)
- `apps/api/tests/unit/coordinator/test_coordinator.py` (new)

**Test delta:** +2 test files, +40 new test cases (21 provider + 19 coordinator)
**Coverage:** 89.91% (118 tests, all passing)

**Deferred followups:** see docs/followups.md

---

## 2026-05-14 | Unit 4 — Phase C: PlannerAgent + Aviasales Adapter + Hunter Agents + Integration

**Summary**
Full Phase C implementation: tool schemas, prompt files, PlannerAgent with LLM eval,
Aviasales adapter with VCR tests, FlightHunterAgent updated to use Aviasales,
HotelHunterAgent unit tests, end-to-end integration test.

Key design decisions:
- Three ToolDefinition objects in `agents/tools.py` (`EXTRACT_TRAVEL_INTENT`,
  `EXTRACT_FLIGHT_OPTIONS`, `EXTRACT_HOTEL_OPTIONS`) use strict JSON Schema
  (`additionalProperties: false`, enum constraints, pattern validation).
- PlannerAgent loads its system prompt from `agents/prompts/planner_system.txt`
  at init time (5-line header comment, `{today}` placeholder replaced at call time).
  Forces tool-call via `extract_travel_intent`; parses with `_parse_intent()`.
- 20 golden examples in `evals/datasets/planner/golden.jsonl`; 20 matching VCR
  cassettes at `tests/fixtures/cassettes/eval/planner/p-{id}.yaml`.
  `evals/run.py` replaced the eval-quick CI dry-run; scores SCORED + OPTIONAL fields,
  exits 0 if accuracy >= 95%.  PlannerAgent hit 100% accuracy on VCR replay.
- Aviasales adapter: httpx.AsyncClient wrapper over Travelpayouts
  `/aviasales/v3/prices_for_dates`; custom error hierarchy
  (`AviasalesError` > `AviasalesRateLimitError` | `AviasalesServerError` |
  `AviasalesClientError`).  Three VCR cassettes (happy path, 429, 5xx).
- FlightHunterAgent accepts optional `AviasalesAdapter`; if provided, calls it
  async and maps raw list[dict] to `FlightOption` via `_map_raw_to_flight_option()`
  (computes arrival times from departure + duration_to/duration_back).
  Falls back to SyntheticProvider when no adapter injected.
- HotelHunterAgent unchanged architecturally; full unit test coverage added.
- Integration test (`tests/integration/test_coordinator_pipeline.py`): mocked
  LLMClient -> PlannerAgent -> Coordinator -> FlightHunterAgent (Synthetic) +
  HotelHunterAgent end-to-end.  Covers BOM-CDG, BOM-NRT, BOM-DPS, star filters,
  budget tracking, planner error handling.

**Files touched:** 22
- `apps/api/src/travel_agent/agents/tools.py` (new)
- `apps/api/src/travel_agent/agents/prompts/planner_system.txt` (new)
- `apps/api/src/travel_agent/agents/planner.py` (implemented from stub)
- `apps/api/src/travel_agent/agents/flight_hunter.py` (updated -- Aviasales support + mapping)
- `apps/api/src/travel_agent/coordinator/state.py` (airline_preference field added to TravelIntent)
- `apps/api/src/travel_agent/providers/aviasales.py` (new)
- `apps/api/evals/run.py` (new)
- `apps/api/evals/datasets/planner/golden.jsonl` (new -- 20 examples)
- `apps/api/evals/datasets/flight_hunter/golden.jsonl` (new -- 7 examples)
- `apps/api/evals/datasets/hotel_hunter/golden.jsonl` (new -- 7 examples)
- `apps/api/tests/fixtures/cassettes/aviasales/{flights_happy_path,flights_429,flights_5xx}.yaml` (3 new)
- `apps/api/tests/fixtures/cassettes/eval/planner/p-{001..020}.yaml` (20 new)
- `apps/api/tests/unit/agents/test_planner.py` (new -- 29 tests, mock LLMClient)
- `apps/api/tests/unit/agents/test_flight_hunter.py` (new -- 20 tests)
- `apps/api/tests/unit/agents/test_hotel_hunter.py` (new -- 20 tests)
- `apps/api/tests/unit/providers/test_aviasales.py` (new -- 6 tests, VCR)
- `apps/api/tests/integration/test_coordinator_pipeline.py` (new -- 9 integration tests)
- `apps/api/.env.example` (AVIASALES_API_KEY added)
- `apps/api/pyproject.toml` (PLR0913 ignore for providers/**)
- `.github/workflows/ci.yml` (eval-quick wired to evals/run.py)
- `docs/migration-log.md` (this entry)

**Test delta:** +5 test files, +76 new test cases (29 planner + 20 flight hunter + 20 hotel hunter + 6 aviasales + 9 integration + ~1 tools)
**Coverage:** 92.64% (194 tests, all passing)

**Deferred followups:** see docs/followups.md

---

## 2026-05-14 | Unit 5A — Demo Path: OptimizerAgent + SSE /search Endpoint

**Summary**
Full demo-path implementation: affiliate deep-link builder, Pareto-frontier scoring
utilities, OptimizerAgent with LLM-generated archetype explanations, Coordinator wired
with OptimizerAgent, SSE streaming endpoint, and DemoAuthMiddleware.

Key design decisions:
- `ArchetypeLabel` StrEnum (was `Archetype`) uses dash-cased values (`"best-value"`,
  `"best-experience"`) for clean JSON serialization.  New `Archetype` BaseModel holds
  label + FlightOption + explanation + deeplink_url + score_breakdown.
- Pareto frontier (`utility/pareto.py`) uses PEP 695 generic function syntax (`[T]`).
  `value_score` = sigmoid on price + layover penalty + red-eye penalty (00-05 departure).
  `experience_score` = linear duration + direct bonus + cabin bonus + daytime bonus.
  Both scores bounded to [0, 1]; all magic numbers extracted to module-level constants.
- OptimizerAgent makes 2 LLM calls per search (one per archetype) using
  `generate_archetype_explanation` tool; falls back to hardcoded text when no LLM client
  or no tool call returned.
- `coordinator/streaming.py` owns the per-window flight loop (not FlightHunterAgent)
  so `search_progress` events can be emitted between windows.  Imports
  `_map_raw_to_flight_option` from flight_hunter to reuse mapping logic.
- DemoAuthMiddleware only enforces when `APP_MODE=demo`; health endpoint is always open
  for Cloud Run readiness probes.
- Aviasales affiliate deep-link uses raw `/search/...` path from API when present;
  constructs `/{ORIGIN}{DD}{MM}{YYYY}{DEST}` fallback otherwise.  Partner sub-ID appended
  as `{marker}.{archetype-label}` for per-archetype attribution.

**Files touched:** 18
- `apps/api/src/travel_agent/providers/aviasales/__init__.py` (refactored from single file)
- `apps/api/src/travel_agent/providers/aviasales/adapter.py` (moved from providers/aviasales.py)
- `apps/api/src/travel_agent/providers/aviasales/deeplink.py` (new)
- `apps/api/src/travel_agent/utility/value.py` (new)
- `apps/api/src/travel_agent/utility/experience.py` (new)
- `apps/api/src/travel_agent/utility/pareto.py` (new)
- `apps/api/src/travel_agent/agents/tools.py` (GENERATE_ARCHETYPE_EXPLANATION added)
- `apps/api/src/travel_agent/agents/prompts/optimizer_system.txt` (new)
- `apps/api/src/travel_agent/agents/optimizer.py` (implemented from stub)
- `apps/api/src/travel_agent/coordinator/state.py` (ArchetypeLabel + Archetype BaseModel + archetypes field)
- `apps/api/src/travel_agent/coordinator/coordinator.py` (OptimizerAgent wired, APP_MODE guard)
- `apps/api/src/travel_agent/coordinator/streaming.py` (new)
- `apps/api/src/travel_agent/api/routes/search.py` (new)
- `apps/api/src/travel_agent/api/middleware/auth.py` (new)
- `apps/api/src/travel_agent/api/main.py` (search router + auth middleware + phase C health)
- `apps/api/tests/unit/providers/test_deeplink.py` (new -- 12 tests)
- `apps/api/tests/unit/utility/test_pareto.py` (new -- 9 tests)
- `apps/api/tests/unit/utility/test_scoring.py` (new -- 13 tests)
- `apps/api/tests/unit/agents/test_optimizer.py` (new -- 12 tests)
- `apps/api/tests/unit/test_main.py` (health phase updated to "C")
- `apps/api/tests/integration/test_search_endpoint.py` (new -- 9 SSE integration tests)
- `apps/api/.env.example` (AVIASALES_PARTNER_ID, APP_MODE, DEMO_API_KEY added)
- `docs/migration-log.md` (this entry)

**Test delta:** +5 test files, +55 new test cases (12 deeplink + 9 pareto + 13 scoring + 12 optimizer + 9 SSE integration)
**Coverage:** 90.11% (249 tests, all passing)
**Eval:** eval-quick 100% (20/20 planner examples)
