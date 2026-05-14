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
