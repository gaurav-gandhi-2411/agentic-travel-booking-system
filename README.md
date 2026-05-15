# DealHunter — Agentic Flight Search

A multi-agent system that takes a natural-language travel request and returns two ranked
flight options (best-value, best-experience) with real Aviasales pricing and one-click
affiliate booking links.  Stream the full agent pipeline over SSE with a single POST.

## Live demo

**https://agentic-travel-booking-system.vercel.app/demo**

Type a natural-language query and watch two specialist agents stream their reasoning in
real time, then surface two ranked flight options with one-click affiliate booking links.

Queries verified against live Aviasales (May 2026):

| Query | Route | Options | Trade-off |
|---|---|---|---|
| Delhi to Dubai in June | DEL→DXB | 16 | GF 1-stop red-eye INR 15,090 vs 6E non-stop morning INR 18,280 (21% price gap) |
| Delhi to Singapore in June | DEL→SIN | 13 | 6E 1-stop evening INR 15,786 vs 6E 1-stop afternoon INR 17,378 (10% gap, 45 min faster) |
| Mumbai to Bangkok for 5 days in June | BOM→BKK | 6 | Non-stop red-eye INR 12,361 vs non-stop afternoon INR 13,591 (10% gap, civilised hour) |

See `docs/demo-queries.md` for full verification data and Travelpayouts API ceiling notes.

## Quick start (local)

```bash
# 1. Copy and fill env (needs ANTHROPIC_API_KEY + AVIASALES_API_KEY + AVIASALES_PARTNER_ID)
cp apps/api/.env.example apps/api/.env
# Set APP_MODE=demo and fill in the three keys above

# 2. Start the API
cd apps/api && uvicorn travel_agent.api.main:app --reload

# 3. Stream a search
curl -N -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $DEMO_API_KEY" \
  -d '{"query": "Delhi to Dubai in June"}'
```

You will see a sequence of SSE events:
```
data: {"type": "planner_started"}
data: {"type": "planner_done", "intent": {...}}
data: {"type": "search_started", "windows": [...]}
data: {"type": "search_progress", "window_idx": 0, "flights_found": 6}
...
data: {"type": "search_done", "total_options": 42}
data: {"type": "optimizer_started"}
data: {"type": "archetype_ready", "archetype": {"label": "best-value", ...}}
data: {"type": "archetype_ready", "archetype": {"label": "best-experience", ...}}
data: {"type": "done"}
```

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, httpx |
| LLM | Anthropic claude-haiku-4-5 (planner) + claude-sonnet-4-6 (optimizer) |
| Flight data | Aviasales / Travelpayouts Data API |
| Scoring | Pareto frontier on (value\_score, experience\_score) axes |
| Affiliate | Travelpayouts deep-link with per-archetype sub-ID |
| Infra | GCP Cloud Run (API), Vercel (web), GitHub Actions + WIF |
| LLM routing | Multi-provider: Ollama (local), OpenRouter/Groq (free), Anthropic (prod) |

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | When `LLM_ROUTING_PROFILE=prod` | Anthropic API key |
| `AVIASALES_API_KEY` | When `APP_MODE=demo` | Travelpayouts Data API key |
| `AVIASALES_PARTNER_ID` | When `APP_MODE=demo` | Travelpayouts affiliate partner ID |
| `APP_MODE` | No (default: `synthetic`) | `synthetic` = no real API calls; `demo` = real Aviasales |
| `DEMO_API_KEY` | When `APP_MODE=demo` | Secret checked against `X-API-Key` header |
| `LLM_ROUTING_PROFILE` | No (default: `local`) | `local` \| `free` \| `prod` \| `eval` |
| `OPENROUTER_API_KEY` | When profile=`free` | OpenRouter key |
| `GROQ_API_KEY` | Optional (free profile fallback) | Groq key |

See `apps/api/.env.example` for the full list.

## Architecture

```
POST /search
  └─ PlannerAgent (haiku-4-5)      — parse NL query → TravelIntent
  └─ per-window flight search       — Aviasales API or SyntheticProvider
  └─ OptimizerAgent (sonnet-4-6)   — Pareto frontier → 2 archetypes + NL explanations
  └─ SSE stream                     — one event per phase transition
```

Six specialist agents in total: Planner, FlightHunter, HotelHunter, Optimizer, Booking,
ConversationManager.  The demo path uses Planner + FlightHunter + Optimizer only.

See `docs/architecture/` for ADRs and `plan.md` for the phased delivery roadmap.

## Development

```bash
# Install dependencies and pre-commit hooks
cd apps/api && pip install -e ".[dev]"

# Lint + typecheck + test
python -m ruff check src/
python -m mypy src/travel_agent/ --ignore-missing-imports
python -m pytest tests/           # 249 tests, ~90% coverage

# Eval harness (planner golden set, VCR replay)
python evals/run.py               # 100% accuracy target
```

## Open-Source Model Track

Alongside the core system, this project fine-tunes compact open-source models
(Qwen 2.5 7B/14B) per agent using QLoRA to match frontier performance on narrow tasks.

- **Eval harness** in `evals/` — golden datasets, runner, and scorer
- **20% of eval golden examples** released publicly (CC-BY-4.0)

See ADRs 0008–0012 in `docs/architecture/adr/`.

## What's next

Phase roadmap and deferred items are tracked in `docs/followups.md` and `plan.md`.
Key upcoming work:

- **Hotel search** — Phase 2, gated on affiliate approvals (Booking.com, Agoda)
- **Conversational refinement** — `BookingAgent` and `ConversationManagerAgent` stubs in `agents/`
- **Open-source model track** — Qwen 2.5 7B/14B QLoRA fine-tuning per agent, eval harness in `evals/`
- **Third archetype** — "best-balance" option surfaced from Pareto frontier

## License

MIT. See `LICENSE`.
