# Agentic Travel Booking System

**Status:** Planning
**Owner:** gaurav-gandhi-2411
**Last updated:** 2026-05-09

---

## 1. Vision

A multi-agent travel booking system that takes a natural-language request like *"Book a flight to Rome with a 3-star accommodation or better"* and finds the best 7-day window in the next 30 days across flights and hotels, presents two ranked package archetypes (best-value, best-experience), supports conversational refinement ("show me cheaper", "no morning flights"), and executes the booking after explicit user authorization.

Sold as a B2B agent layer that travel platforms (Skyscanner, MakeMyTrip, Kayak-likes) can drop on top of their existing inventory APIs. Their data, our brain. Our positioning is **agent-as-a-layer**, not aggregator-as-a-product.

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

### Non-Goals (v1)
- Real merchant-of-record bookings on real money. Booking happens via (a) Amadeus/Duffel test-mode confirmations, or (b) affiliate deep-link handoff to airline/OTA.
- Train, bus, car-rental, activity bookings.
- Group/family bookings beyond 2 adults + 2 children.
- Visa/passport workflow.
- Reward-points or miles optimization (Phase 2 candidate).

## 3. Positioning

**The sellable artifact is the agent layer, not the data.** Skyscanner and MakeMyTrip already have inventory; what they don't have is:

1. A reasoning agent that does multi-window arbitrage in 30 seconds.
2. A scoring model that produces *value* and *experience* archetypes, not just cheapest-first.
3. A conversational refinement UX that survives 3+ turns.
4. A booking flow with auditable HITL, idempotency, and clean rollback.

The repo must read as a drop-in SDK: clean interfaces, tenant-scoped state, OpenAPI spec, ADRs, runbooks, load test reports. The buyer's tech lead reviews this before procurement signs anything.

## 4. Architecture Overview

### 4.1 The agents

Five specialist agents and one coordinator. Each agent is a stateless function that takes shared state in, returns shared state out. The coordinator is deterministic code (no LLM); only the agents call Claude.

| Agent | Role | Model |
|---|---|---|
| **PlannerAgent** | Parses natural-language input into a structured `TravelIntent` (origin, destination, dates flexibility, accommodation constraints, traveler count, budget hints). | Sonnet |
| **FlightHunterAgent** | Given a `TravelIntent` and a candidate window, queries flight providers (Amadeus, Duffel) in parallel via adapters, normalizes results into `FlightOption[]`, applies hard filters. | Haiku for parsing, Sonnet for synthesis |
| **HotelHunterAgent** | Same for hotels. Amadeus only in v1. Returns `HotelOption[]` with rating ≥ user constraint. | Haiku + Sonnet |
| **OptimizerAgent** | Takes the cross-product of flight × hotel × window candidates, scores each on the value and experience utility functions, returns the Pareto frontier and the two archetype winners. Explains the choices in natural language. | Sonnet |
| **BookingAgent** | Drives the HITL booking flow: locks an offer, presents to user, waits for explicit confirmation, executes booking (test-mode or affiliate redirect), records in audit log. | Sonnet |
| **ConversationManagerAgent** | Owns the user-facing dialogue. Routes refinement requests ("cheaper", "different dates") back into the pipeline by mutating `TravelIntent` and re-running the relevant agents. | Sonnet |

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

Stored encrypted at rest using GCP KMS (near-free: ~$0.06/key-version/month + $0.03/10K ops; Cloud Run has native integration). Decrypted in-memory only at request time.

## 9. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| **Backend** | Python 3.12, FastAPI, Pydantic v2 | Async-native, Pydantic v2 perf, ecosystem maturity |
| **Frontend** | Next.js 15 (App Router), React 19, TailwindCSS, shadcn/ui | Vercel default, SSR for SEO on the marketing pages |
| **LLM SDK** | `anthropic` Python SDK with tool use | Native tool use, prompt caching support |
| **Database** | Neon (Postgres free tier) | Serverless, branching for staging, generous free tier |
| **Cache & rate limit** | Upstash Redis (free tier) | Serverless Redis, REST API, free plan covers v1 |
| **Queue** | None in v1 (FastAPI background tasks); add Cloud Tasks if needed | Avoid premature distribution |
| **LLM** | Claude (Sonnet 4.6 + Haiku 4.5) | Sonnet for reasoning, Haiku for parsing |
| **Travel APIs** | Amadeus Self-Service (flights + hotels), Duffel (flights) | Real data, free dev tier, sandboxed booking |
| **Auth (tenant)** | API key + JWT for user sessions | Standard for B2B SDK |
| **Hosting (API)** | Cloud Run + WIF | Reuses gaurav's existing GCP setup, always-free quota |
| **Hosting (web)** | Vercel | Free tier, Next.js native |
| **Secrets** | GCP Secret Manager + KMS (near-free) | Reuses triage-iq pattern; KMS at ~$0.06/key-version/month |
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
│   │   │   ├── providers/
│   │   │   │   ├── base.py           # Protocol
│   │   │   │   ├── amadeus_flight.py
│   │   │   │   ├── amadeus_hotel.py
│   │   │   │   └── duffel_flight.py
│   │   │   ├── scoring/
│   │   │   │   ├── utility.py
│   │   │   │   ├── pareto.py
│   │   │   │   └── components.py
│   │   │   ├── tenancy/
│   │   │   │   ├── auth.py
│   │   │   │   ├── rls.py
│   │   │   │   ├── rate_limit.py
│   │   │   │   └── credentials.py
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
│   │   ├── tests/
│   │   │   ├── unit/
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
│   │   ├── adr/                      # Architecture Decision Records
│   │   │   ├── 0001-multi-agent-coordinator-pattern.md
│   │   │   ├── 0002-provider-adapter-pattern.md
│   │   │   ├── 0003-affiliate-vs-merchant-of-record.md
│   │   │   ├── 0004-postgres-rls-for-tenancy.md
│   │   │   ├── 0005-hierarchical-window-search.md
│   │   │   └── 0006-pareto-frontier-archetypes.md
│   │   ├── system-overview.md
│   │   ├── data-model.md
│   │   └── sequence-diagrams/
│   ├── runbooks/
│   │   ├── on-call.md
│   │   ├── provider-outage.md
│   │   ├── tenant-onboarding.md
│   │   └── booking-rollback.md
│   ├── api/
│   │   └── openapi.yaml              # Auto-generated, served at /docs
│   └── customer/
│       ├── integration-guide.md
│       ├── webhook-spec.md
│       └── pricing-model.md
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
- ADRs 0001–0006 written.

### Phase 1 — Provider Adapters + Search (Week 2)
- `FlightProvider` and `HotelProvider` Protocols.
- Amadeus flight + hotel adapters, Duffel flight adapter.
- Contract tests using recorded fixtures (VCR.py).
- Per-tenant credential loading.
- Hard call-budget enforcement.

### Phase 2 — Coordinator + Window Search (Week 3)
- Hierarchical sampling algorithm.
- L1 + L2 + L3 caching layers.
- Cost ledger.
- Integration tests on full window-search path with mocked providers.

### Phase 3 — Agents (Week 4)
- Five agents implemented with strict input/output schemas.
- Prompt files in `apps/api/src/dealhunter/agents/prompts/` — version controlled, eval-able.
- Anthropic prompt caching enabled on system prompts.
- Tool-use definitions for each agent.
- Agent-level eval suite (golden inputs → expected schema outputs).

### Phase 4 — Scoring + Pareto (Week 5)
- Utility functions implemented and unit-tested with synthetic options.
- Pareto frontier extraction.
- Archetype selection logic.
- Natural-language explanation generation.

### Phase 5 — Booking Flow (Week 6)
- HITL state machine.
- Idempotency layer.
- Audit log.
- Test-mode booking against Amadeus + Duffel sandboxes.
- Affiliate redirect builder.
- Rollback flow with compensating cancellations.

### Phase 6 — Conversation + Refinement (Week 7)
- ConversationManagerAgent.
- Refinement loop: parse "cheaper" / "skip mornings" / "different city area" → mutate `TravelIntent` → re-run relevant agents only (not full pipeline).
- Multi-turn session state in Postgres.
- Token-budget enforcement per session.

### Phase 7 — Multi-tenancy Hardening (Week 8)
- API key issuance flow.
- Postgres RLS policies on all tables.
- Per-tenant rate limiting.
- Tenant-scoped logs + traces.
- Per-tenant scoring-weight overrides.

### Phase 8 — Frontend (Week 9)
- Next.js chat UI on Vercel.
- SSR marketing pages (landing, pricing, docs).
- Authenticated app: chat with the agent, see archetype packages, refine, confirm.
- Real-time streaming of agent reasoning.

### Phase 9 — Observability + SLOs (Week 10)
- OTel instrumentation across the stack.
- Three SLOs: search-completion-rate, p95-latency, booking-success-rate.
- Cloud Monitoring dashboards + alerts.
- Sentry wired in.

### Phase 10 — Load + Production Cutover (Week 11)
- k6 load tests in CI: 50 concurrent users, 5-minute soak.
- Staging → prod canary deploy via Cloud Run revisions.
- Runbooks finalized.
- Customer-facing OpenAPI docs site.

### Phase 11 — Sales Enablement (Week 12)
- Demo tenant with pre-loaded affiliate config.
- Sandbox API keys for prospects.
- Pricing-model doc, integration guide, webhook spec.
- One-page architecture deck.

## 12. Testing Strategy

| Layer | Approach | Coverage Target |
|---|---|---|
| **Unit** | pytest, fixtures, no I/O | ≥ 85% |
| **Integration** | pytest with testcontainers (Postgres) and mocked providers | All happy paths + key error paths |
| **Provider contract** | VCR.py recorded fixtures vs live sandbox in nightly job | All adapter methods |
| **Agent eval** | Golden inputs → schema-validated outputs, scored by a judge prompt | 10–20 cases per agent (Phase 3 sign-off); 50+ stretch target deferred to Phase 11 |
| **End-to-end** | Playwright against staging | 5 critical user journeys |
| **Load** | k6, 50 concurrent, 5-min soak | p95 < 4s, error rate < 1% |
| **Security** | bandit, pip-audit, npm audit, secret scan | Zero high/critical |

Total project coverage target: **≥ 80%**.

## 13. Observability

### 13.1 Distributed tracing
OTel auto-instrumentation on FastAPI + httpx + asyncpg + Anthropic SDK. Every request gets a trace ID propagated through agents → coordinator → providers. Exported to Cloud Trace.

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
- **Secrets:** GCP Secret Manager + KMS. Tenant provider credentials encrypted at rest. Never logged.
- **Transport:** TLS 1.3 everywhere. HSTS on web. Cloud Run enforces HTTPS.
- **Dependency hygiene:** pip-audit + npm audit + Dependabot (matches triage-iq pattern).
- **Static analysis:** bandit (Python), ESLint (TS), secret scanning (gitleaks in CI).
- **Threat model:** STRIDE doc in `docs/architecture/threat-model.md`.
- **Vulnerability disclosure:** SECURITY.md + security@ contact.
- **Data retention:** PII purged 90 days after last user activity (configurable per tenant).
- **No PII in logs.** No raw provider responses with traveler data persisted beyond audit table.

## 15. Cost Model

Per-request cost components, tracked in the cost ledger:

```
total_cost = sum(llm_cost) + sum(provider_call_cost)

llm_cost (typical request):
  Planner:           ~600 in / 200 out (Sonnet)        → $0.005
  FlightHunter ×8:   ~400 in / 150 out each (Haiku)    → $0.008
  HotelHunter ×8:    ~400 in / 150 out each (Haiku)    → $0.008
  Optimizer:         ~3000 in / 500 out (Sonnet)       → $0.017
  ConversationMgr:   ~800 in / 300 out (Sonnet)        → $0.007
  Refinement turns:  ~half of above per turn

  Subtotal (1 search + 1 refinement): ~$0.07
  With prompt caching enabled:        ~$0.03

provider_call_cost: $0 (free dev tier within quota)

Per booking: ~$0.10–$0.15 amortized.
```

Cost ledger exposes this as a per-tenant metric. Tenants can set monthly cost ceilings — when hit, requests degrade gracefully (smaller window sampling, no refinement allowed). Cost visibility: shown in tenant admin dashboard by default; hidden from end-user chat UI unless tenant enables `show_cost_to_users: true` in their config.

## 16. CI/CD

- **Trigger:** push to any branch → CI; merge to main → staging deploy; tag `v*` → prod deploy.
- **CI stages:** lint → type-check → unit → integration (with services in compose) → contract → security scans → build artifact.
- **Deploy:** WIF auth → Cloud Run revision → 5% traffic canary → smoke test → 100% promotion. Rollback via Cloud Run revision pinning.
- **DB migrations:** Alembic, run via `pre-deploy` Cloud Run job, gated on success before app revision goes live.
- **Frontend:** Vercel auto-deploy on main.
- **Secret rotation:** runbook + GitHub Action template for quarterly rotation.

## 17. Sellability Checklist

What a buyer's tech lead expects to see in the repo before procurement signs:

- [ ] OpenAPI spec served at /docs and downloadable
- [ ] ADRs explaining every load-bearing decision
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

## 18. Open Decisions and Risks

### Risks

1. **Amadeus/Duffel rate limits in dev tier** may bite on real demos. Mitigation: aggressive cache + per-tenant credentials means each prospect uses their own quota.
2. **LLM cost at scale.** $0.03–$0.10/request adds up. Mitigation: cost ledger + tenant-set ceilings + prompt caching + Haiku for high-volume tasks.
3. **Multi-agent debugging.** Even with the coordinator pattern, traces will be deep. Mitigation: every agent call gets a span, trace IDs propagate, Cloud Trace UI is good enough.
4. **Affiliate program acceptance.** Some networks gate on traffic. Mitigation: launch with one accepted network (CJ or Awin), expand later.
5. **Test-mode bookings drifting from real production behavior.** Mitigation: nightly contract tests against live sandbox, not just recorded fixtures.

### Open decisions — resolved at project kickoff

- **Frontend auth:** Clerk free tier. NextAuth requires DIY tenant modeling; Supabase Auth conflicts with Neon. Clerk's org model aligns with multi-tenant from v1. Rationale documented in ADR-0007 (Phase 8).
- **LLM SDK:** Anthropic Python SDK on backend (only option); Vercel AI SDK on Next.js frontend for streaming UX only — we control the API shape, so no lock-in risk. Rationale in ADR-0008 (Phase 8).
- **WindowSearcher:** Deterministic coordinator code, not an agent. Despite the §4.2 diagram visually placing it alongside agents, it is coordinator code that dispatches to agents. An LLM agent here would add latency, cost, and unpredictability with no benefit. Clarified in ADR-0005.
- **Cost ledger end-user visibility:** Hidden from end-user chat UI by default. Tenant admin dashboard always shows cost. Tenants opt end-users in via `show_cost_to_users: true` in tenant config.

## 19. Definition of Done (v1)

- All Phase 0–11 milestones complete.
- ≥80% test coverage, all SLOs green for 7 consecutive days on staging.
- 50-concurrent-user load test passes targets.
- Demo tenant with full sandbox booking flow works end-to-end on prod URL.
- Public OpenAPI docs site live.
- Runbooks reviewed and exercised in a tabletop drill.
- One external reviewer (your choice) signs off on the architecture.
- README good enough that a new dev can be productive in a day.

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
