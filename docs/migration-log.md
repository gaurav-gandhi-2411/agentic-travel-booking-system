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
