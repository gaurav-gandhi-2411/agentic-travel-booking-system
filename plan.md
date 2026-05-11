# Agentic Travel Booking System

**Status:** Planning
**Owner:** gaurav-gandhi-2411
**Last updated:** 2026-05-10

---

## 1. Vision

DealHunter is the reasoning layer for travel platforms. Instead of showing users a list of flights sorted by price, it identifies the best 7-day window across a 30-day horizon, ranks packages on value vs experience trade-offs, explains each recommendation in natural language, and refines conversationally across multiple turns.

The agent ships on fine-tuned 7B open-source models that we benchmark against frontier — with published numbers and reproducible eval. Plug in your inventory via the adapter pattern; we bring the brain.

Sold as a B2B agent layer for travel platforms (Skyscanner, MakeMyTrip, Kayak-likes). They have inventory; they don't have a deeply-reasoning agent that does multi-window arbitrage, two-archetype ranking with NL explanations, and conversational refinement that survives 3+ turns. Our positioning is agent-as-a-layer, not aggregator-as-a-product.

## 1b. The Two Tracks

This project runs two parallel tracks that share infrastructure and codebase but have distinct deliverables and success criteria.

**Product track** — a production-grade, multi-tenant, sellable SDK:
- Multi-agent coordinator with deterministic dispatch (ADR-0001)
- Provider adapter layer for Amadeus and Duffel (ADR-0002)
- Pareto-frontier package optimization with natural-language explanations (ADR-0006)
- HITL booking flow with idempotency and audit log (ADR-0003)
- Multi-tenant isolation via Postgres RLS (ADR-0004)
- Cloud Run deployment on GCP with WIF and Secret Manager

**Research track** — an open-source model fine-tuning and evaluation effort:
- Per-agent acceptance thresholds vs frontier baselines (ADR-0009)
- Eval harness with automated CI gates and reproducible results (ADR-0010)
- Dataset generation via OpenRouter teacher with self-critique and human QA (ADR-0011)
- QLoRA fine-tuning of Qwen 2.5 7B (narrow agents) and 14B (hard agents) via unsloth
- Published LoRA adapters, eval sample, methodology, and technical report (ADR-0012)

Both tracks share: agents, coordinator, provider adapters, the LLM provider abstraction (ADR-0008), and the eval golden datasets (which are never used for training).

## 2. Goals and Non-Goals

### Goals
- Natural-language travel intent parsing with structured slot extraction.
- Multi-agent orchestration with a deterministic coordinator (not free-form agent chat).
- 7-day window optimization across a 30-day horizon with smart sampling and caching.
- Two ranked package archetypes: **best-value** and **best-experience**.
- Conversational refinement loop ("cheaper", "skip red-eyes", "different hotel area").
- Authorization-gated booking with idempotency, time-boxed offer holds, and rollback semantics.
- Multi-tenant from v1: per-tenant API keys, rate limits, provider credentials, and affiliate IDs.
- Production observability: distributed tracing, SLOs, alerting, audit log.
- Cost transparency: per-request token cost tracked and exposed to tenants.
- **Open-source model fine-tuning track:** QLoRA fine-tuning of Qwen 2.5 7B (narrow agents) and 14B (hard agents) with per-agent acceptance thresholds vs frontier baselines.
- **Reproducible eval harness:** automated CI gates, golden datasets, judge prompts, and JSON result artifacts sufficient for independent reproduction.
- **Research publication:** technical report PDF, Hugging Face model cards, and 20% eval sample dataset published under open licenses.
- **Defensible USP grounded in four specific artifacts:** window-optimization algorithm with documented ADR, fine-tuned open-source models with HF-published benchmarks, production-grade engineering posture (40+ commits of discipline before business logic), conversational refinement that re-enters at the right phase based on what changed.

### Non-Goals (v1)
- Real merchant-of-record bookings on real money. Booking happens via (a) Amadeus/Duffel test-mode confirmations, or (b) affiliate deep-link handoff to airline/OTA.
- Train, bus, car-rental, activity bookings.
- Group/family bookings beyond 2 adults + 2 children.
- Visa/passport workflow.
- Reward-points or miles optimization (Phase 2 candidate).
- **Matching frontier models on conversational coherence at 7B–14B scale.** The ConversationManagerAgent acceptance bar is 35% wins-or-ties vs frontier (ADR-0009). If the fine-tuned model does not reach this bar, it ships on the OpenRouter free-tier 70B fallback. This limitation is acknowledged and documented in the technical report.

## 3. Positioning

**The sellable artifact is the agent layer, not the data.** Skyscanner and MakeMyTrip already have inventory; what they don't have is:

1. A reasoning agent that does multi-window arbitrage in 30 seconds.
2. A scoring model that produces *value* and *experience* archetypes, not just cheapest-first.
3. A conversational refinement UX that survives 3+ turns.
4. A booking flow with auditable HITL, idempotency, and clean rollback.

The repo must read as a drop-in SDK: clean interfaces, tenant-scoped state, OpenAPI spec, ADRs, runbooks, load test reports. The buyer's tech lead reviews this before procurement signs anything.

## 4. Architecture Overview

### 4.1 The agents

Six specialist agents coordinated by deterministic dispatch code. Each agent is a stateless function that takes shared state in, returns shared state out. The coordinator is deterministic code (no LLM); only the agents call LLMs.

| Agent | Role | Model (eval profile) |
|---|---|---|
| **PlannerAgent** | Parses natural-language input into a structured `TravelIntent` (origin, destination, dates flexibility, accommodation constraints, traveler count, budget hints). | Sonnet |
| **FlightHunterAgent** | Given a `TravelIntent` and a candidate window, queries flight providers (Amadeus, Duffel) in parallel via adapters, normalizes results into `FlightOption[]`, applies hard filters. | Haiku for parsing, Sonnet for synthesis |
| **HotelHunterAgent** | Same for hotels. Amadeus only in v1. Returns `HotelOption[]` with rating ≥ user constraint. | Haiku + Sonnet |
| **OptimizerAgent** | Takes the cross-product of flight × hotel × window candidates, scores each on the value and experience utility functions, returns the Pareto frontier and the two archetype winners. Explains the choices in natural language. | Sonnet |
| **BookingAgent** | Drives the HITL booking flow: locks an offer, presents to user, waits for explicit confirmation, executes booking (test-mode or affiliate redirect), records in audit log. | Sonnet |
| **ConversationManagerAgent** | Owns the user-facing dialogue. Routes refinement requests ("cheaper", "different dates") back into the pipeline by mutating `TravelIntent` and re-running the relevant agents. | Sonnet |

> *Model assignments above reflect the `eval` routing profile (frontier baselines). In the
> `local` and `free` profiles, agents route to Qwen 2.5 7B/14B or OpenRouter free-tier 70B
> models respectively. Per-agent model routing is configurable via `llm_routing.yaml`.
> See ADR-0008 and §20.*

### 4.2 The coordinator pattern

```
User → ConversationManager → Coordinator (deterministic)
                                  │
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
              Planner       WindowSearcher    Optimizer
                              │
                  ┌───────────┴────────────┐
                  ▼                        ▼
            FlightHunter             HotelHunter
                  │                        │
              [Adapters]               [Adapters]
              Amadeus, Duffel          Amadeus
```

Agents never call each other. The coordinator dispatches and merges. State flows through a single `RequestState` object (Pydantic). This is the design that survives debugging.

### 4.3 The Provider Adapter pattern

```python
class FlightProvider(Protocol):
    async def search(self, query: FlightQuery) -> list[FlightOption]: ...
    async def lock_offer(self, option_id: str) -> OfferLock: ...
    async def confirm_booking(self, lock: OfferLock, traveler: Traveler) -> BookingResult: ...
    async def cancel_offer(self, lock: OfferLock) -> None: ...

class AmadeusFlightProvider(FlightProvider): ...
class DuffelFlightProvider(FlightProvider): ...
```

Adding a third provider in v2 is a new class, no orchestration changes. Same protocol for hotels.

## 5. The Window-Search Algorithm

The combinatorial space: 24 candidate 7-day start dates × N flight options × M hotels = potentially 500+ provider calls per request. We can't and shouldn't call all of them.

### 5.1 Hierarchical sampling

**Stage 1 — Coarse sweep.** Sample every 3rd start date (8 windows). For each, fetch top-3 flights and top-5 hotels (≤ 11 calls/window × 8 = ~88 calls). Cache aggressively — same window queried twice in 24h hits cache.

**Stage 2 — Drill-down.** Take the top-3 windows by interim score. For each, expand to ±2 adjacent days (up to 12 additional windows). Re-query with deeper depth (top-10 flights, top-15 hotels).

**Stage 3 — Pareto extraction.** From all collected options, compute value and experience utilities, extract the Pareto frontier, surface the archetype winners.

**Early termination.** If Stage 1 finds a window with score > stage-2 expected gain, skip Stage 2.

### 5.2 Caching strategy

- **L1 (in-process LRU):** route + date + provider hash → response. 5-minute TTL.
- **L2 (Upstash Redis):** same key, 30-minute TTL, shared across replicas.
- **L3 (Postgres):** persistent provider-call ledger for dedup across sessions and for cost-attribution analytics.

Cache is tenant-scoped — never cross-leak between tenants.

### 5.3 Call budget

Per request, hard cap at:
- 150 flight provider calls
- 100 hotel provider calls
- 20 LLM calls

If exceeded, the agent returns partial results with a "limited search" disclosure. Logged as a degraded-experience event for SLO tracking.

## 6. Scoring and Ranking

### 6.1 Utility functions

```
value_score = w1 * (1/total_cost) 
            + w2 * (flight_quality_score)
            + w3 * (hotel_value_score)
            + w4 * (window_desirability)

experience_score = w1' * (flight_comfort_score)
                 + w2' * (hotel_rating × hotel_review_score)
                 + w3' * (location_centrality)
                 + w4' * (refundability_score)
                 - penalty_for_total_cost_over_p75
```

Initial weights are hand-tuned; v2 candidate is to learn weights from user pick-rate per archetype.

### 6.2 Component scores

- **flight_quality_score** — function of: layover count, layover duration, departure time desirability, airline reputation, on-time history (where Amadeus exposes it).
- **hotel_value_score** — function of: stars, review score, price-per-night vs market p25, distance from destination centroid.
- **window_desirability** — peak vs shoulder, weekend coverage, calendar weight (configurable per tenant — they may know their users prefer weekends).
- **refundability_score** — change/cancel fees normalized; non-refundable = 0, fully flexible = 1.

### 6.3 Pareto frontier

Best-value and best-experience are extracted from the Pareto frontier (no option dominates them on both cost and quality). This guarantees the two archetypes are *meaningfully different*, not just two slices of the same ranking.

### 6.4 Explanation

OptimizerAgent generates 2–3 sentences per archetype: *"Best value: Air France direct flight on the 14th, Hotel Artemide rated 4.6 with breakfast, total ₹62,400 — saves ₹18k vs the next-best by avoiding a peak-weekend departure."* This is what users actually need; raw price tables are commodity output.

## 7. Booking Flow

### 7.1 Two modes

**Test mode (default for v1 demos).** Booking calls hit Amadeus/Duffel test endpoints, get back fake but well-formed PNRs. Used for end-to-end demos, integration tests, and tenant onboarding.

**Affiliate mode (production revenue path).** Instead of executing, generate a deep link to the airline/OTA with the tenant's affiliate ID, pre-populate as much as possible, hand off. User completes booking on partner site; we earn affiliate commission. Compliant, free of merchant-of-record obligations, monetizable from day 1.

Tenant config picks the mode per environment.

### 7.2 HITL contract

```
1. OptimizerAgent surfaces 2 packages.
2. User picks one (or refines: "cheaper" → loops back).
3. BookingAgent calls provider.lock_offer() → time-boxed hold (typically 10-15 min).
4. UI shows: "Confirm booking? Total ₹62,400. Holds expire in 14:32."
5. User explicitly confirms (button + textual confirmation in chat).
6. BookingAgent calls provider.confirm_booking() with idempotency key.
7. Audit log entry written (tenant, user, package, provider response, timestamp, hash).
8. Confirmation surfaced to user with PNR/reference + receipt.
```

### 7.3 Idempotency and rollback

- Every confirm_booking call includes `Idempotency-Key: {tenant_id}:{request_id}:{user_action_hash}`. Replay-safe.
- If booking succeeds on flight but fails on hotel: BookingAgent triggers compensating cancellation on flight side (test mode supports it; affiliate mode is a no-op since user does the booking).
- Hold expiry: if user doesn't confirm within window, BookingAgent calls cancel_offer and surfaces a refresh prompt.

### 7.4 Audit log

Append-only Postgres table, never updated, never deleted. Schema:

```sql
CREATE TABLE booking_audit (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL,
  user_id UUID NOT NULL,
  request_id UUID NOT NULL,
  action TEXT NOT NULL,  -- 'lock', 'confirm', 'cancel', 'expire'
  package_hash TEXT NOT NULL,
  provider_response JSONB,
  total_cost_minor INTEGER,
  currency TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  idempotency_key TEXT UNIQUE
);
```

This is what enterprise procurement asks for in week one.

## 8. Multi-Tenancy

### 8.1 Tenant model

```
Tenant (id, name, plan, status, created_at)
  ├── ApiKey (tenant_id, key_hash, scopes, expires_at)
  ├── ProviderCredential (tenant_id, provider, env, encrypted_secret)
  ├── AffiliateConfig (tenant_id, provider, affiliate_id, commission_rate)
  ├── RateLimitConfig (tenant_id, requests_per_minute, requests_per_day)
  └── ScoringWeights (tenant_id, archetype, weights_json)
```

### 8.2 Isolation

- All tables tenant-scoped with `tenant_id` column.
- Postgres Row-Level Security policies: every query filtered by `current_setting('app.tenant_id')`.
- Connection pool sets `app.tenant_id` at session start based on API key.
- Cache keys include `tenant_id`.
- Logs and traces tagged with `tenant_id` (for filtering, not for cross-tenant access).

### 8.3 Per-tenant rate limiting

Upstash Redis token bucket: `ratelimit:{tenant_id}:{minute_window}`. Free plan supports the throughput we need for v1.

### 8.4 Per-tenant provider credentials

Tenants provide their own Amadeus/Duffel API keys. We never use a shared credential pool — that's both a contractual violation with the providers and a security disaster on multi-tenant.

Stored encrypted at rest using **AES-256-GCM at the application layer** (ADR-0007). A single 32-byte master key lives in GCP Secret Manager (`tenant-credential-master-key`). Decrypted in-memory only at request time. Quarterly rotation via `docs/runbooks/master-key-rotation.md` (Phase 7).

## 9. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| **Backend** | Python 3.12, FastAPI, Pydantic v2 | Async-native, Pydantic v2 perf, ecosystem maturity |
| **Frontend** | Next.js 15 (App Router), React 19, TailwindCSS, shadcn/ui | Vercel default, SSR for SEO on the marketing pages |
| **LLM abstraction** | `LLMClient` Protocol (ADR-0008): OllamaAdapter (local default), OpenRouterAdapter (free tier), GroqAdapter (fallback), AnthropicAdapter (eval baseline only, off by default) | Vendor-agnostic, swappable per agent via `llm_routing.yaml` |
| **LLM (production)** | Qwen 2.5 7B/14B via Ollama (`local` profile) or OpenRouter free-tier 70B models (`free` profile) | $0 runtime cost on free tier; local Ollama for deterministic dev |
| **LLM (eval baseline)** | Claude Sonnet 4.6 (manual, via Claude.ai) + Llama 3.3 70B / Qwen 2.5 72B (automated, OpenRouter free) | Frontier baseline for research track without API spend |
| **Fine-tuning** | unsloth + transformers + PEFT (QLoRA, 4-bit) | RTX 3070 local + Colab/Kaggle free tier; no cost |
| **Eval tooling** | `evals/run.py` (custom), datasets library, evaluate library | Reproducible, CI-integrated, publishable methodology |
| **Database** | Neon (Postgres free tier) | Serverless, branching for staging, generous free tier |
| **Cache & rate limit** | Upstash Redis (free tier) | Serverless Redis, REST API, free plan covers v1 |
| **Queue** | None in v1 (FastAPI background tasks); add Cloud Tasks if needed | Avoid premature distribution |
| **Travel APIs** | Travelpayouts Aviasales Data API (cached flight pricing, free, India-accepted); Synthetic provider (`SyntheticFlightProvider` + `SyntheticHotelProvider`) for CI, gap-filling, and demos (ADR-0013, ADR-0014) | Zero cost; covers full v1 scope without provider approval gating |
| **Auth (tenant)** | API key + JWT for user sessions | Standard for B2B SDK |
| **Hosting (API)** | Cloud Run + WIF | Reuses gaurav's existing GCP setup, always-free quota |
| **Hosting (web)** | Vercel | Free tier, Next.js native |
| **Secrets** | GCP Secret Manager (AES-GCM in app layer, ADR-0007) | $0 incremental cost; KMS deferred to commercial tier |
| **Observability** | OpenTelemetry → Cloud Trace + Cloud Logging + Cloud Monitoring | Free tier covers v1 volume |
| **Error tracking** | Sentry (free tier) | 5K errors/month free |
| **Load testing** | k6 (open source) | Local + GitHub Actions |
| **CI/CD** | GitHub Actions, WIF to GCP | Reuses triage-iq pattern |

## 10. Repository Structure

```
agentic-travel-booking-system/
├── apps/
│   ├── api/                          # FastAPI backend
│   │   ├── src/travel_agent/
│   │   │   ├── agents/
│   │   │   │   ├── planner.py
│   │   │   │   ├── flight_hunter.py
│   │   │   │   ├── hotel_hunter.py
│   │   │   │   ├── optimizer.py
│   │   │   │   ├── booking.py
│   │   │   │   └── conversation.py
│   │   │   ├── coordinator/
│   │   │   │   ├── coordinator.py
│   │   │   │   ├── window_search.py
│   │   │   │   └── state.py
│   │   │   ├── llm/                  # LLM provider abstraction (ADR-0008)
│   │   │   │   ├── base.py           # LLMClient Protocol, LLMRequest, LLMResponse
│   │   │   │   ├── anthropic.py      # Eval baseline only; off by default
│   │   │   │   ├── openrouter.py
│   │   │   │   ├── groq.py
│   │   │   │   ├── ollama.py         # Default for local dev
│   │   │   │   ├── routing.py        # Config loader + profile selection
│   │   │   │   └── __init__.py       # get_llm_client() factory
│   │   │   ├── providers/
│   │   │   │   ├── base.py           # Protocol
│   │   │   │   ├── aviasales/        # Travelpayouts Aviasales Data API adapter
│   │   │   │   │   └── flight.py
│   │   │   │   └── synthetic/        # SyntheticFlightProvider + SyntheticHotelProvider
│   │   │   │       ├── flight.py
│   │   │   │       ├── hotel.py
│   │   │   │       └── data/         # Bundled JSON realism data (ADR-0014)
│   │   │   ├── scoring/
│   │   │   │   ├── utility.py
│   │   │   │   ├── pareto.py
│   │   │   │   └── components.py
│   │   │   ├── tenancy/
│   │   │   │   ├── auth.py
│   │   │   │   ├── rls.py
│   │   │   │   ├── rate_limit.py
│   │   │   │   ├── credentials.py
│   │   │   │   └── crypto.py         # AES-GCM encrypt/decrypt (ADR-0007)
│   │   │   ├── persistence/
│   │   │   │   ├── models.py
│   │   │   │   ├── audit.py
│   │   │   │   └── migrations/
│   │   │   ├── api/
│   │   │   │   ├── routes/
│   │   │   │   │   ├── search.py
│   │   │   │   │   ├── booking.py
│   │   │   │   │   ├── tenant.py
│   │   │   │   │   └── health.py
│   │   │   │   └── middleware/
│   │   │   ├── observability/
│   │   │   │   ├── tracing.py
│   │   │   │   ├── metrics.py
│   │   │   │   └── cost_ledger.py
│   │   │   └── config.py
│   │   ├── config/
│   │   │   └── llm_routing.yaml      # Per-agent model routing (local/free/eval profiles)
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   │   └── llm/              # Protocol conformance, routing, factory tests
│   │   │   ├── integration/
│   │   │   ├── contract/             # Provider adapter contract tests
│   │   │   └── load/                 # k6 scripts
│   │   ├── pyproject.toml
│   │   └── Dockerfile
│   │
│   └── web/                          # Next.js frontend
│       ├── app/
│       │   ├── (marketing)/          # Public pages
│       │   ├── (app)/                # Authenticated chat UI
│       │   └── api/                  # Vercel route handlers (proxy to backend)
│       ├── components/
│       ├── lib/
│       └── package.json
│
├── evals/                            # Eval harness (ADR-0010)
│   ├── datasets/                     # Golden sets — never used for training
│   │   ├── planner/golden.jsonl
│   │   ├── flight_hunter/golden.jsonl
│   │   ├── hotel_hunter/golden.jsonl
│   │   ├── optimizer/golden.jsonl
│   │   ├── booking/golden.jsonl
│   │   ├── conversation/golden.jsonl
│   │   └── README.md                 # Provenance, format spec
│   ├── judges/                       # Pairwise preference prompts (Optimizer, Conversation)
│   │   ├── optimizer.txt
│   │   ├── conversation.txt
│   │   └── README.md
│   ├── lib/                          # Runner, scorer, judge invocation
│   │   ├── runner.py
│   │   ├── scorer.py
│   │   └── judge.py
│   ├── results/                      # JSON results per run (.gitignore large runs)
│   │   └── README.md                 # Results schema, comparison protocol
│   ├── manual/                       # Claude.ai spot-check records
│   │   └── README.md
│   ├── tests/                        # Tests for the eval runner itself
│   └── run.py                        # Entry point: python evals/run.py --agent planner
│
├── scripts/
│   └── dataset/                      # Dataset generation pipeline (ADR-0011)
│       ├── generate.py               # OpenRouter teacher generation, rate-limited, resumable
│       ├── critique.py               # Self-critique second pass
│       ├── ingest_qa.py              # Paste-back tool for Claude.ai QA results
│       ├── diversity_matrix.py       # destinations × profiles × budgets × ambiguity levels
│       └── README.md
│
├── packages/
│   └── shared-types/                 # OpenAPI-generated TypeScript types
│
├── infra/
│   ├── terraform/                    # GCP + Neon + Upstash provisioning
│   ├── github/                       # Workflow templates
│   └── observability/                # Dashboard + alert YAML
│
├── docs/
│   ├── architecture/
│   │   ├── adr/
│   │   │   ├── 0001-multi-agent-coordinator-pattern.md
│   │   │   ├── 0002-provider-adapter-pattern.md
│   │   │   ├── 0003-affiliate-vs-merchant-of-record.md
│   │   │   ├── 0004-postgres-rls-for-tenancy.md
│   │   │   ├── 0005-hierarchical-window-search.md
│   │   │   ├── 0006-pareto-frontier-archetypes.md
│   │   │   ├── 0007-defer-kms-aes-gcm-application-layer.md
│   │   │   ├── 0008-multi-provider-llm-abstraction.md
│   │   │   ├── 0009-open-source-model-strategy.md
│   │   │   ├── 0010-eval-harness-design.md
│   │   │   ├── 0011-dataset-generation-pipeline.md
│   │   │   └── 0012-publishing-strategy.md
│   │   ├── system-overview.md
│   │   ├── data-model.md
│   │   └── sequence-diagrams/
│   ├── runbooks/
│   │   ├── cloud-setup.md
│   │   ├── on-call.md
│   │   ├── provider-outage.md
│   │   ├── tenant-onboarding.md
│   │   ├── booking-rollback.md
│   │   └── master-key-rotation.md    # Phase 7 — AES-GCM key rotation protocol
│   ├── research/                     # Research track workspace (ADR-0012)
│   │   ├── README.md
│   │   ├── paper-outline.md
│   │   ├── experiment-log.md
│   │   ├── model-card-template.md
│   │   ├── dataset-card-template.md
│   │   └── benchmark-protocol.md
│   ├── api/
│   │   └── openapi.yaml              # Auto-generated, served at /docs
│   └── customer/
│       ├── integration-guide.md
│       ├── webhook-spec.md
│       └── pricing-model.md
│
├── models/                           # Fine-tuned checkpoints (gitignored — push to HF)
│   └── .gitkeep
│
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── deploy-staging.yml
│       ├── deploy-prod.yml
│       ├── load-test.yml
│       ├── pip-audit.yml
│       └── dependabot.yml
│
├── plan.md
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE
```

## 11. Phased Delivery

### Phase 0 — Foundations (Week 1)
- Repo scaffolding, monorepo setup (`apps/api`, `apps/web`), pyproject + package.json.
- GCP project, WIF, Secret Manager, Cloud Run service stub.
- Neon DB provisioned, baseline schema, migrations framework (Alembic).
- Upstash Redis provisioned.
- CI: lint, type-check, unit tests, smoke deploy to staging.
- ADRs 0001–0012 written.
- Minimum FastAPI module and Dockerfile sufficient for staging-deploy workflow verification (Stage 0.4 end). Real API surface in Phase 1.

### Phase 0.5 — Marketing Frontend MVP (3–5 days, gating dependency)
- Minimal Next.js site deployed to Vercel: landing page, "how it works", contact/waitlist form.
- No chat UI, no auth. Static or minimal-dynamic. Sole purpose: satisfy the "credible public website" requirement for hotel affiliate program applications (Booking.com, Agoda, Hotellook).
- Applications submitted immediately after deploy: Booking.com, Agoda, Hotellook, Trip.com.
- This phase gates real hotel data in Phase 1 and beyond. Calendar gating: Phase 1 starts in parallel; hotel providers become available as approvals land.

### Phase 1 — Provider Adapters + Search (Week 2)

Phase 1 starts from the Phase 0 baseline: an empty travel_agent package, a minimum main.py with one /health endpoint, and a working Dockerfile. The first Phase 1 commit expands the API surface beyond this baseline.

- `FlightProvider` and `HotelProvider` Protocols.
- Travelpayouts Aviasales Data API adapter (`aviasales/flight.py`).
- `SyntheticFlightProvider` and `SyntheticHotelProvider` with bundled realism data (ADR-0014).
- `synthetic_when_unavailable` routing flag per agent (ADR-0013).
- Contract tests for Aviasales adapter; statistical property tests for Synthetic provider.
- Per-tenant credential loading and AES-GCM decryption.
- Hard call-budget enforcement.

### Phase 2 — Coordinator + Window Search (Week 3)
- Hierarchical sampling algorithm.
- L1 + L2 + L3 caching layers.
- Cost ledger.
- Integration tests on full window-search path with mocked providers.

### Phase 2.5 — LLM Provider Abstraction + Eval Skeleton (Week 4)
- `LLMClient` Protocol + adapters (Ollama, OpenRouter, Groq, Anthropic stub).
- `llm_routing.yaml` with three profiles (`local`, `free`, `eval`).
- `get_llm_client(agent)` factory; protocol-conformance unit tests.
- `evals/` directory scaffold: `run.py`, `lib/runner.py`, `lib/scorer.py`, `lib/judge.py`.
- Makefile targets: `eval-quick`, `eval-full`, `eval-baselines`.
- CI eval job (paths-filter trigger, `continue-on-error: false`).

### Phase 3 — Agents (Week 5–6)
- Six agents implemented with strict input/output schemas.
- Prompt files in `apps/api/src/travel_agent/agents/prompts/` — version controlled, eval-able.
- Anthropic prompt caching enabled on system prompts (eval profile).
- Tool-use definitions for each agent.
- Agent-level unit tests with mock `LLMClient`.

### Phase 3.5 — Baseline Benchmarks (Week 7)
- Golden datasets seeded: 20–30 examples per agent (enough for initial baseline).
- Baseline run: `eval-full` against Qwen 2.5 72B + Llama 3.3 70B via OpenRouter.
- Manual frontier spot-check: Claude Sonnet 4.6 via Claude.ai, 10 examples per agent.
- Baseline numbers committed to `evals/results/baseline/` and documented in `docs/research/experiment-log.md`.
- Acceptance thresholds from ADR-0009 validated against actual baseline (amend ADR if needed).

### Phase 4 — Scoring + Pareto (Week 8)
- Utility functions implemented and unit-tested with synthetic options.
- Pareto frontier extraction.
- Archetype selection logic.
- Natural-language explanation generation.

### Phase 5 — Booking Flow (Week 9)
- HITL state machine.
- Idempotency layer.
- Audit log.
- Test-mode booking against Amadeus + Duffel sandboxes.
- Affiliate redirect builder.
- Rollback flow with compensating cancellations.

### Phase 6 — Conversation + Refinement (Week 10)
- ConversationManagerAgent.
- Refinement loop: parse "cheaper" / "skip mornings" / "different city area" → mutate `TravelIntent` → re-run relevant agents only (not full pipeline).
- Multi-turn session state in Postgres.
- Token-budget enforcement per session.

### Phase 6.5 — Dataset Generation (Weeks 11–13, mostly unattended)
- Diversity matrix finalized for all six agents.
- `scripts/dataset/generate.py` running unattended against OpenRouter free tier (~50 req/day).
- Self-critique pass (`critique.py`) applied after each generation batch.
- Target: 1,000 training + 100 eval examples per agent (6,600 total).
- Hand-curated golden eval set from Phase 3.5 (20–30 examples per agent) is preserved as the primary eval set. Phase 6.5's machine-generated eval examples are added alongside, not in replacement. The hand-curated set remains the trusted reference for cross-baseline comparison; the machine-generated set provides volume for stable judge-model scoring. Provenance tag in dataset card distinguishes the two.
- Stage 2 QA: 100 examples per agent pasted into Claude.ai, results ingested via `ingest_qa.py`.
- ~3–6 hours active human time total; ~2–3 weeks calendar time due to rate limits.

### Phase 6.6 — Fine-Tuning Round 1 — Qwen 2.5 7B, Narrow Agents (Weeks 14–15)
- QLoRA fine-tuning via unsloth on local RTX 3070.
- Agents: Planner, FlightHunter, HotelHunter, Booking.
- LoRA rank 16, 4-bit NF4, sequence length 2,048.
- ~6–10 hours training per agent; results saved to `models/` and pushed to Hugging Face private repo.
- Training runs logged in `docs/research/experiment-log.md`.
- Priority order: Planner > FlightHunter ≈ HotelHunter > Booking. Booking's threshold (100% state-machine correctness) is achievable on the 70B fallback alone; if calendar pressure forces a drop, Booking ships on fallback rather than blocking the phase.

### Phase 6.7 — Eval + Iterate (Week 16)
- `eval-full` against the Phase 6.6 adapters.
- Compare against Phase 3.5 baselines; check acceptance thresholds (ADR-0009).
- Agents that pass threshold: mark as shipping variant.
- Agents that fall short: document gap, assign 70B fallback in `llm_routing.yaml`.
- One iteration of hyperparameter tuning if a near-miss agent is within 5%.

### Phase 7 — Multi-Tenancy Hardening (Week 17)
- API key issuance flow.
- Postgres RLS policies on all tables.
- Per-tenant rate limiting.
- Tenant-scoped logs + traces.
- Per-tenant scoring-weight overrides.
- `docs/runbooks/master-key-rotation.md` written and exercised.

### Phase 8 — Frontend (Week 18)
- Next.js chat UI on Vercel.
- SSR marketing pages (landing, pricing, docs) — extends the Phase 0.5 marketing site in-place; no separate deploy.
- Authenticated app: chat with the agent, see archetype packages, refine, confirm.
- Real-time streaming of agent reasoning (Vercel AI SDK on frontend; backend streams via SSE).

### Phase 8.5 — Fine-Tuning Round 2 — Qwen 2.5 14B, Hard Agents (Weeks 19–21)
- QLoRA fine-tuning on Colab/Kaggle free tier (T4 or A100 high-RAM runtime).
- Agents: Optimizer, ConversationManager.
- LoRA rank 32, 4-bit NF4, sequence length 2,048.
- ~4–8 hours training per agent per run; multiple runs expected.
- Results pushed to Hugging Face private repo; `eval-full` run after each checkpoint.

### Phase 9 — Observability + SLOs (Week 22)
- OTel instrumentation across the stack.
- Three SLOs: search-completion-rate, p95-latency, booking-success-rate.
- Cloud Monitoring dashboards + alerts.
- Sentry wired in.

### Phase 10 — Load + Production Cutover (Week 23)
- k6 load tests in CI: 50 concurrent users, 5-minute soak.
- Staging → prod canary deploy via Cloud Run revisions.
- Runbooks finalized.
- Customer-facing OpenAPI docs site.

### Phase 11 — Sales Enablement (Week 24)
- Demo tenant with pre-loaded affiliate config.
- Sandbox API keys for prospects.
- Pricing-model doc, integration guide, webhook spec.
- One-page architecture deck.

### Phase 11.5 — Research Writeup + HF Publishing (Weeks 25–26)
- Technical report drafted in `docs/research/` following `paper-outline.md`.
- All eval results compiled; tables and ablations finalized.
- LoRA adapters that passed acceptance threshold published to Hugging Face Hub (CC-BY-NC-4.0).
- 20% golden eval sample published to Hugging Face Datasets (CC-BY-4.0).
- Benchmark reproduction verified: `make eval` against published adapters + published sample returns reported numbers ±2%.
- `benchmark-protocol.md` finalized with full-sample instructions and confidence interval guidance.
- Technical report PDF generated and linked from README and blog post.

## 12. Testing Strategy

| Layer | Approach | Coverage Target |
|---|---|---|
| **Unit** | pytest, fixtures, no I/O | ≥ 85% |
| **Integration** | pytest with testcontainers (Postgres) and mocked providers | All happy paths + key error paths |
| **Provider contract** | VCR.py recorded fixtures vs live sandbox in nightly job | All adapter methods |
| **LLM abstraction** | Protocol-conformance tests with mock `LLMClient`; routing config validation | All adapters + profiles |
| **Agent eval (eval-quick)** | `evals/run.py --subset 20`, triggers on changes to agents/llm/eval files; ~2 min | Regression gate: >2% drop fails CI |
| **Agent eval (eval-full)** | `evals/run.py`, nightly on main; ~30 min | Regression opens GitHub issue |
| **End-to-end** | Playwright against staging | 5 critical user journeys |
| **Load** | k6, 50 concurrent, 5-min soak | p95 < 4s, error rate < 1% |
| **Security** | bandit, pip-audit, npm audit, secret scan | Zero high/critical |

Total project coverage target: **≥ 80%**.

Eval CI trigger paths (no `continue-on-error`):
- `apps/api/src/travel_agent/agents/**`
- `apps/api/src/travel_agent/llm/**`
- `evals/datasets/**`
- `evals/judges/**`
- `evals/run.py`
- Nightly schedule: `cron: '0 2 * * *'` UTC (~7:30 AM IST)
- Manual: `workflow_dispatch`

## 13. Observability

### 13.1 Distributed tracing
OTel auto-instrumentation on FastAPI + httpx + asyncpg. Every request gets a trace ID propagated through agents → coordinator → providers. Exported to Cloud Trace.

### 13.2 SLOs
- **search-completion-rate** ≥ 99% (request returns ranked archetypes within budget)
- **p95-latency** ≤ 8s (NL input → archetypes presented)
- **booking-success-rate** ≥ 95% (lock + confirm round-trip succeeds when user approves)

Each SLO has an error budget burn-rate alert.

### 13.3 Metrics
- Per-tenant: requests/min, error rate, p50/p95/p99 latency, token cost ($), provider call count.
- Per-agent: invocation count, latency, token usage, schema-validation failure rate.
- Per-provider: call count, error rate, p95 latency, rate-limit headroom.

### 13.4 Logs
Structured JSON logs, indexed in Cloud Logging. Every log line carries `tenant_id`, `request_id`, `user_id`, `agent_name` (where applicable). PII scrubbing layer before log emission.

### 13.5 Audit
Booking audit log queryable by tenant via authenticated API. Retention 7 years (compliance posture for regulated buyers).

## 14. Security Posture

- **AuthN:** API key (B2B) + JWT (end-user sessions). Keys hashed in DB.
- **AuthZ:** Postgres RLS on every table. Defense-in-depth at app layer.
- **Secrets:** GCP Secret Manager + AES-GCM at app layer (ADR-0007). Tenant provider credentials encrypted at rest. Never logged.
- **Transport:** TLS 1.3 everywhere. HSTS on web. Cloud Run enforces HTTPS.
- **Dependency hygiene:** pip-audit + npm audit + Dependabot (matches triage-iq pattern).
- **Static analysis:** bandit (Python), ESLint (TS), secret scanning (gitleaks in CI).
- **Threat model:** STRIDE doc in `docs/architecture/threat-model.md`.
- **Vulnerability disclosure:** SECURITY.md + security@ contact.
- **Data retention:** PII purged 90 days after last user activity (configurable per tenant).
- **No PII in logs.** No raw provider responses with traveler data persisted beyond audit table.

## 15. Cost Model

**Budget constraint:** $0 in API spend (LLM, GPU, third-party APIs). Production runtime infrastructure runs at a ~$0.80/month floor (Secret Manager active-version cost beyond the 6-version free tier). Human time is the only meaningful variable cost during development.

### Infrastructure (monthly, pre-launch)

| Service | Cost |
|---|---|
| Cloud Run (staging + prod) | $0 (always-free: 2M req/month, 360K vCPU-sec) |
| Secret Manager (~13 secrets) | ~$0.80/month |
| Cloud Scheduler (1 job) | $0 (free tier) |
| Cloud Trace / Logging / Monitoring | $0 (free tier covers v1 volume) |
| Neon Postgres | $0 (free tier) |
| Upstash Redis | $0 (free tier) |
| Vercel | $0 (Hobby plan) |
| GitHub Actions | $0 (free tier) |
| **Total** | **~$0.80/month** |

### LLM runtime cost

**`local` profile (Ollama, RTX 3070):** $0 per request. Electricity negligible.

**`free` profile (OpenRouter + Groq):** $0 per request. Rate limit (~50 req/day on OpenRouter free models) is the binding constraint, not cost. For sustained demo traffic, the `local` profile or a self-hosted vLLM instance is the recommended path.

**`eval` profile (Anthropic):** Never active in CI or production. Manual baseline runs use Claude.ai web interface (no API spend). $0 API cost.

### Research track cost

| Activity | API cost | Active human time |
|---|---|---|
| Dataset generation — Stage 1 (per 6 agents) | $0 | ~2 hrs scheduling + monitoring |
| Dataset QA — Stage 2 (per agent) | $0 (Claude.ai manual) | ~30–60 min |
| Baseline benchmark run (Phase 3.5) | $0 | ~1 day setup + analysis |
| Fine-tuning Round 1 — 4 narrow agents (local GPU) | $0 | ~1 day active; ~6–10 hrs training |
| Fine-tuning Round 2 — 2 hard agents (Colab/Kaggle) | $0 | ~1 day active; ~4–8 hrs training |
| Eval + iterate (Phase 6.7) | $0 | ~3 days |
| Research writeup + HF publishing (Phase 11.5) | $0 | ~2 weeks writing |

**Total API spend across all phases: $0.**
**Estimated active human time for research track: ~15–25 hours (excluding writing).**

### Call budget enforcement

The `CallBudget` on `RequestState` (ADR-0001) enforces per-request hard caps:
- 150 flight provider calls
- 100 hotel provider calls
- 20 LLM calls

On the `free` profile, OpenRouter's daily rate limit is the binding constraint between requests. The 20-LLM-call per-request cap remains active regardless of profile.

### Cost ledger (tenant-facing)

The cost ledger tracks token counts and equivalent frontier model cost for transparency — even when actual runtime cost is $0. This is relevant when fine-tuned adapters are deployed on tenant-owned GPU infrastructure and tenants want to quantify the compute cost of each request. Cost is shown in the tenant admin dashboard; hidden from end-user chat UI by default unless `show_cost_to_users: true` is set.

## 16. CI/CD

- **Trigger:** push to any branch → CI; merge to main → staging deploy; tag `v*` → prod deploy.
- **CI stages:** lint → type-check → unit → integration (with services in compose) → contract → eval-quick (on path-filter) → security scans → build artifact.
- **Deploy:** WIF auth → Cloud Run revision → 5% traffic canary → smoke test → 100% promotion. Rollback via Cloud Run revision pinning.
- **DB migrations:** Alembic, run via `pre-deploy` Cloud Run job, gated on success before app revision goes live.
- **Frontend:** Vercel auto-deploy on main.
- **Secret rotation:** runbook + GitHub Action template for quarterly rotation.

## 17. Sellability Checklist

What a buyer's tech lead expects to see in the repo before procurement signs:

### Buyer-facing USP artifacts

- [ ] **Window-optimization algorithm** documented end-to-end: ADR-0005 (algorithm rationale), §5 (implementation spec), eval results showing recommendation quality vs brute-force baseline.
- [ ] **Fine-tuned open-source models with HF-published benchmarks:** LoRA adapters on HF Hub (CC-BY-NC-4.0); per-agent eval results vs 70B frontier baseline; reproducible `make eval` in under 30 min from published checkpoints.
- [ ] **Production-grade engineering posture:** 40+ discipline commits before business logic; clean interfaces; ADRs 0001–0014; runbooks for top 5 incidents; load test report; OpenAPI spec.
- [ ] **Conversational refinement that re-enters at the right phase:** ConversationManagerAgent routes "cheaper" to window re-search, "different hotel" to hotel re-rank, "skip red-eyes" to flight filter — not a full pipeline restart.

### Standard procurement checklist

- [ ] OpenAPI spec served at /docs and downloadable
- [ ] ADRs explaining every load-bearing decision (0001–0014)
- [ ] Threat model document
- [ ] Runbooks for the top 5 incidents (provider outage, booking rollback failure, tenant credential rotation, hot tenant rate-limit, on-call escalation)
- [ ] Load test report committed in `docs/performance/`
- [ ] Architecture deck (1-pager + detailed PDF)
- [ ] Integration guide with code samples in TS, Python, curl
- [ ] Webhook spec for booking events
- [ ] SOC2-readiness checklist (even if not certified yet — shows posture)
- [ ] Pricing model with per-call, per-booking, per-tenant tiers
- [ ] Demo tenant with synthetic data and a public sandbox
- [ ] Status page (free Better Stack tier)
- [ ] Hugging Face model cards for each fine-tuned agent adapter (CC-BY-NC-4.0, PEFT format)
- [ ] Technical report PDF in `docs/research/` with reproducible benchmark methodology and per-agent results
- [ ] Reproducible benchmark: `make eval` against published HF checkpoints + published 20% eval sample returns reported numbers ±2%
- [ ] Eval dataset sample on Hugging Face Datasets (20% of golden sets, CC-BY-4.0, with provenance)

## 18. Open Decisions and Risks

### Risks

1. **Amadeus/Duffel rate limits in dev tier** may bite on real demos. Mitigation: aggressive cache + per-tenant credentials means each prospect uses their own quota.
2. **LLM quality on `free` profile.** OpenRouter 70B free models are strong but not frontier. Mitigation: fine-tuned 7B adapters for narrow agents; 70B fallback for hard agents; `eval` profile for technical demos.
3. **Fine-tuning timeline.** Phase 6.5 dataset generation is rate-limited to ~50 req/day; calendar time is 2–3 weeks even with minimal active effort. Mitigation: start generation early, run unattended overnight.
4. **Multi-agent debugging.** Even with the coordinator pattern, traces will be deep. Mitigation: every agent call gets a span, trace IDs propagate, Cloud Trace UI is good enough.
5. **Affiliate program acceptance.** Some networks gate on traffic. Mitigation: launch with one accepted network (CJ or Awin), expand later.
6. **Test-mode bookings drifting from real production behavior.** Mitigation: nightly contract tests against live sandbox, not just recorded fixtures.

### Open decisions — resolved at project kickoff

- **Frontend auth:** Clerk free tier. NextAuth requires DIY tenant modeling; Supabase Auth conflicts with Neon. Clerk's org model aligns with multi-tenant from v1. ADR to be written in Phase 8 (number TBD after ADR-0012).
- **LLM abstraction:** Multi-provider routing with `LLMClient` Protocol (ADR-0008). Ollama for local dev, OpenRouter/Groq for free-tier cloud, Anthropic off by default. Vercel AI SDK on Next.js frontend for streaming UX only; the backend API shape is ours, so no vendor lock-in on the frontend.
- **WindowSearcher:** Deterministic coordinator code, not an agent. Despite the §4.2 diagram visually placing it alongside agents, it is coordinator code that dispatches to agents. Clarified in ADR-0005.
- **Cost ledger end-user visibility:** Hidden from end-user chat UI by default. Tenant admin dashboard always shows cost. Tenants opt end-users in via `show_cost_to_users: true` in tenant config.
- **KMS deferred:** AES-GCM in application layer with master key in Secret Manager (ADR-0007). KMS deferred until commercial revenue justifies the cost and operational complexity.

## 19. Definition of Done (v1)

- All Phase 0–11.5 milestones complete.
- ≥80% test coverage, all SLOs green for 7 consecutive days on staging.
- 50-concurrent-user load test passes targets.
- Demo tenant with full sandbox booking flow works end-to-end on prod URL.
- Public OpenAPI docs site live.
- Runbooks reviewed and exercised in a tabletop drill.
- One external reviewer (your choice) signs off on the architecture.
- README good enough that a new dev can be productive in a day.
- Technical report published; reproducible benchmark validated by at least one independent run.
- All passing fine-tuned adapters published to Hugging Face Hub.

## 20. Open-Source Model Strategy

This section summarizes the research track decisions documented in ADRs 0008–0012. The
canonical source of truth for each decision is the respective ADR; this section is a
cross-cutting overview.

### LLM provider abstraction (ADR-0008)

All agent code calls an `LLMClient` Protocol (vendor-agnostic). Concrete adapters:
`OllamaAdapter` (local, default), `OpenRouterAdapter` (cloud free tier), `GroqAdapter`
(fallback), `AnthropicAdapter` (eval baseline only, off by default). Per-agent model
routing is configured in `apps/api/config/llm_routing.yaml` with three named profiles:
`local`, `free`, `eval`. Profile switched via `LLM_ROUTING_PROFILE` env var.

### Model selection and acceptance thresholds (ADR-0009)

Primary fine-tuning target: **Qwen 2.5 7B Instruct** (Apache 2.0). Secondary for hard
agents: **Qwen 2.5 14B** (LoRA only). Fine-tuning via QLoRA + unsloth on RTX 3070 (7B)
and Colab/Kaggle free tier (14B).

Per-agent acceptance thresholds vs frontier baseline (Qwen 2.5 72B / Llama 3.3 70B):

| Agent | Metric | Threshold |
|---|---|---|
| PlannerAgent | Schema-correctness | ≥ 98% of frontier |
| FlightHunterAgent | Filter + extraction accuracy | ≥ 95% of frontier |
| HotelHunterAgent | Filter + extraction accuracy | ≥ 95% of frontier |
| OptimizerAgent | Pairwise judge preference | ≥ 40% wins-or-ties |
| BookingAgent | State-machine correctness | 100% (tied) |
| ConversationManagerAgent | Pairwise judge preference (5 turns) | ≥ 35% wins-or-ties |

Agents below threshold ship on the OpenRouter free-tier 70B fallback. This is an honest
outcome, not a failure.

### Eval harness (ADR-0010)

Three components: golden datasets (`evals/datasets/<agent>/golden.jsonl`), judge prompts
(`evals/judges/<agent>.txt`), and test runner (`evals/run.py`). Two CI modes:
- `eval-quick` (20 examples, every PR, ~2 min) — regression gate, no escape hatch.
- `eval-full` (100 examples, nightly, ~30 min) — regression opens GitHub issue.

Judge model: Qwen 2.5 72B (OpenRouter free) primary; Llama 3.3 70B cross-check. No
frontier judge in CI. Regression defined as >2% drop on any metric.

### Dataset generation (ADR-0011)

Two-stage pipeline:
1. **Programmatic:** Qwen 2.5 72B teacher via OpenRouter free tier (~50 req/day).
   Self-critique chain: generate → critique → regenerate if flagged. Diversity matrix
   covers destinations × traveler profiles × budget tiers × ambiguity levels.
   Target: 1,000 training + 100 eval examples per agent.
2. **Manual QA:** 100 examples per agent reviewed via Claude.ai with rubric prompt.
   Results ingested via `scripts/dataset/ingest_qa.py`.

Total cost: $0 API spend. ~15–25 hours human time across all agents.

### Publishing (ADR-0012)

| Artifact | License | Where |
|---|---|---|
| Fine-tuned LoRA adapter weights | CC-BY-NC-4.0 (applies to adapter delta only; base model is Apache 2.0 and obtained separately from Hugging Face) | Hugging Face Hub |
| Eval methodology + runner code | MIT | This repo |
| 20% golden dataset sample | CC-BY-4.0 | Hugging Face Datasets |
| Dataset generation scripts | MIT | This repo (`scripts/dataset/`) |
| Technical report | CC-BY-4.0 | `docs/research/` + blog |

80% golden dataset and training data are retained as proprietary. The reproduction
scripts are published so the methodology is reproducible even if the exact data is not.

---

## Appendix A — Glossary

- **TravelIntent:** structured representation of user request post-Planner.
- **RequestState:** full state object passed between agents within one user request.
- **Window:** a 7-day candidate (start_date, end_date).
- **Archetype:** best-value or best-experience package.
- **Pareto frontier:** set of options where no other option dominates on both cost and quality.
- **HITL:** human-in-the-loop.
- **WIF:** Workload Identity Federation (GCP keyless auth from GitHub Actions).
- **RLS:** Row-Level Security (Postgres).
- **Idempotency key:** deterministic identifier ensuring repeated calls have a single effect.
- **QLoRA:** quantized Low-Rank Adaptation — 4-bit fine-tuning method that fits large models in consumer GPU VRAM.
- **LoRA adapter:** a small set of learned weight deltas added on top of a frozen base model.
- **LLM routing profile:** named configuration (`local`, `free`, `eval`) that maps each agent to a specific model and provider.
- **eval-quick:** 20-example eval subset run in CI on relevant file changes (~2 min).
- **eval-full:** complete golden dataset eval run nightly on main (~30 min).
