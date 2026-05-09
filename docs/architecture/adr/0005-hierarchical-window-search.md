# ADR-0005: Hierarchical Window Search Algorithm and WindowSearcher Classification

**Status:** Accepted — 2026-05-09

---

## Context

### The search problem

Given a user travel request with flexible dates, the system must find the best 7-day
window within a 30-day horizon. The combinatorial space is large:

- 24 possible start dates (a 7-day window starting on each day of a 30-day horizon,
  with the last 6 days excluded since they don't fit a full 7-day window)
- Per window: top-K flights per direction × top-M hotels × 2 providers (Amadeus, Duffel)

At full depth (top-10 flights, top-15 hotels per window, 2 providers):
```
24 windows × (10 Amadeus flights + 10 Duffel flights + 15 Amadeus hotels) ≈ 840 calls
```

This exceeds the hard call budget (150 flight calls, 100 hotel calls) by ~5×, and would
take 3–5 minutes on real provider APIs. Users expect results in under 8 seconds (p95 SLO).

The challenge is to find a near-optimal solution within the budget without knowing in
advance which windows will be productive.

### The WindowSearcher ambiguity

The coordinator diagram in plan.md §4.2 places `WindowSearcher` as a box alongside
`Planner` and `Optimizer`:

```
User → ConversationManager → Coordinator
                                  │
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
              Planner       WindowSearcher    Optimizer
```

This visual placement — combined with the fact that `WindowSearcher` dispatches to
`FlightHunterAgent` and `HotelHunterAgent`, which do call Claude — raised the question
during plan review: **is `WindowSearcher` an LLM agent?**

The answer is no. This ADR clarifies the classification and the algorithm.

---

## Decision

### Part A: WindowSearcher is deterministic coordinator code, not an LLM agent

`WindowSearcher` is a Python class that lives in
`apps/api/src/travel_agent/coordinator/window_search.py`. It:
- Contains **no LLM calls**.
- Makes no calls to the Anthropic SDK.
- Is invoked by the `Coordinator` as a subroutine, not as an `Agent`.
- Dispatches to `FlightHunterAgent` and `HotelHunterAgent` (which do call Claude), but
  is itself pure algorithmic code.

The §4.2 diagram places `WindowSearcher` at the coordinator level because it is the
coordinator's primary workhorse during the search phase — it consumes the budget,
runs the sampling stages, and returns structured results to the coordinator. The diagram
is not showing that `WindowSearcher` is an agent; it is showing the coordinator's
internal decomposition.

**The rule:** if a component calls the Anthropic SDK, it is an agent. If it does not,
it is coordinator code regardless of its complexity or position in the diagram.

`WindowSearcher` does not call the Anthropic SDK. It is coordinator code.

### Part B: The 3-stage hierarchical sampling algorithm

The algorithm is implemented in `coordinator/window_search.py` and is deterministic
given the same inputs and provider responses.

**Stage 1 — Coarse sweep**

Sample every 3rd start date across the 30-day horizon: 8 windows. For each window,
fetch the top-3 cheapest flights and top-5 cheapest hotels using the configured provider
adapters. Cache results aggressively.

Approximate call count:
- Flights: 8 windows × (3 Amadeus + 3 Duffel) = 48 flight calls
- Hotels: 8 windows × 5 Amadeus hotel calls = 40 hotel calls
- LLM: `FlightHunterAgent` (Haiku) × 8 + `HotelHunterAgent` (Haiku) × 8 = 16 LLM calls

Each window is scored with an interim utility score using a lightweight version of the
scoring functions (no Sonnet call at this stage — just the utility math).

**Early termination.** If the top window's interim score exceeds the expected gain from
Stage 2 by a configurable margin (`STAGE2_EXPECTED_GAIN_THRESHOLD`), Stage 2 is skipped.
This typically triggers when there is a dominant window (e.g., a significantly cheaper
weekend departure with good hotel availability).

**Stage 2 — Drill-down**

Take the top-3 windows from Stage 1 by interim score. For each, expand to ±2 adjacent
start dates (up to 5 new dates per window = up to 15 new windows, minus any already
covered by Stage 1). For these expansion windows, fetch deeper:
- Top-10 flights, top-15 hotels per window.

Approximate additional call count (assuming no early termination, full expansion):
- Flights: 15 windows × (10 Amadeus + 10 Duffel) = 300 calls → exceeds budget
- Actual behavior: Stage 2 budget is capped at the remaining budget after Stage 1.
  If Stage 1 used 48 flight calls, Stage 2 has 102 remaining. The coordinator
  reduces the depth (top-K) proportionally until the budget fits.

**Stage 3 — Pareto extraction**

All `FlightOption[]` and `HotelOption[]` from Stages 1 and 2 (deduplicated by
`raw_provider_ref`) are handed to `OptimizerAgent`. The optimizer computes the full
utility scores and extracts the Pareto frontier. `WindowSearcher` is not involved in
Stage 3 — it returns all collected candidates to the coordinator, which passes them
to `OptimizerAgent`.

**Budget enforcement**

`RequestState` carries a `CallBudget` object that is mutated throughout the search:

```python
class CallBudget(BaseModel):
    flight_calls_used: int = 0
    hotel_calls_used: int = 0
    llm_calls_used: int = 0
    flight_calls_max: int = 150
    hotel_calls_max: int = 100
    llm_calls_max: int = 20

    def can_call_flight(self) -> bool:
        return self.flight_calls_used < self.flight_calls_max

    def can_call_hotel(self) -> bool:
        return self.hotel_calls_used < self.hotel_calls_max

    def can_call_llm(self) -> bool:
        return self.llm_calls_used < self.llm_calls_max
```

`WindowSearcher` checks `budget.can_call_flight()` before each provider call. If the
budget is exhausted mid-search, it marks the search as `degraded` in `RequestState`,
returns the candidates collected so far, and the coordinator surfaces a "limited search"
disclosure alongside the results (logged as a degraded-experience event for SLO tracking).

**Caching**

Before any provider call, `WindowSearcher` checks the L2 cache (Upstash Redis):
```
key: "{tenant_id}:flight:{origin}:{destination}:{start_date}:{provider}:{depth}"
TTL: 30 minutes
```

A cache hit does not consume budget. This is the primary mechanism for handling
repeated searches (e.g., a user refreshes or starts a new session with the same intent).

---

## Consequences

**Positive:**
- The algorithm is fully deterministic given the same input and provider responses,
  making it unit-testable with mocked providers (no live API calls needed).
- The budget is enforced at the `WindowSearcher` level, not distributed across agents.
  If the budget is about to be exceeded, `WindowSearcher` degrades gracefully rather
  than hard-failing.
- Stage 2 only expands the most promising windows, focusing the remaining budget where
  it adds the most value.
- Early termination in Stage 1 skips Stage 2 entirely when the optimal window is
  obvious — this is the common case for popular routes with clear seasonal patterns.
- The `degraded` flag is propagated to the SLO metric `search-completion-rate`, keeping
  SLO accounting honest. Degraded results are partial, not errors.
- The `WindowSearcher` classification as deterministic code (not an agent) means it can
  be tested, profiled, and optimized without LLM costs or variability.

**Negative:**
- Stage 1's coarse sampling (every 3rd day) can miss a narrow optimal window that falls
  between two sampled dates. Stage 2 partially mitigates this by expanding ±2 days
  around the top-3 Stage 1 winners, but a window at day N+1 where N and N+3 are both
  sampled will only be found if N or N+3 places in the top-3.
- The budget split between Stage 1 and Stage 2 is heuristic, not learned. A route with
  high variance (e.g., TATL routes with very different pricing by day of week) may need
  deeper Stage 1 sampling. This is a Phase 2 candidate for per-route tuning.
- `STAGE2_EXPECTED_GAIN_THRESHOLD` is a hand-tuned parameter. Wrong values mean either
  always running Stage 2 (overcautious, slower) or always skipping Stage 2 (may miss
  better options). Initial value is 15% score improvement.

**Neutral:**
- `WindowSearcher` does not call LLMs; `FlightHunterAgent` and `HotelHunterAgent` do.
  The LLM budget counter is managed at the agent level (incremented when an agent calls
  `client.messages.create()`), not at the `WindowSearcher` level. This keeps LLM budget
  accounting close to where LLM calls happen.
- The 24 candidate windows assume a 30-day horizon. If the user specifies a fixed date,
  the coordinator bypasses `WindowSearcher` and runs a single-window search directly.

---

## Alternatives Considered

### Alternative 1: Exhaustive search

Query all 24 windows at full depth (top-10 flights, top-15 hotels).

**Rejected because:**
- Approximately 840 provider calls, exceeding the 250-call budget by 3.4×.
- At provider API latencies (200–800ms per call with retries), this is 2–10 minutes
  of wall-clock time, violating the p95 ≤ 8s SLO.
- Provider rate limits (Amadeus self-service free tier: ~10 calls/second) mean 840 calls
  take at least 84 seconds even ignoring latency.

### Alternative 2: LLM-driven adaptive sampling

Replace the deterministic 3-stage algorithm with a Sonnet agent that decides which
windows to sample next, based on preliminary results:

```
LLM: "I've seen that weekends in weeks 2 and 3 are expensive. Sample week 1 weekdays next."
```

**Rejected because:**
- Every "which window next?" decision is an additional LLM call, potentially 5–10 calls
  for the sampling strategy alone. Against a 20-call LLM budget, this leaves only 10–15
  calls for the actual agent work (Planner, FlightHunter, HotelHunter, Optimizer).
- The adaptive sampling strategy itself is not testable with golden inputs — it depends
  on LLM behavior, which adds variability to what should be a deterministic algorithm.
- Budget enforcement is harder: an LLM that decides to sample 30 windows instead of 8
  cannot be cheaply stopped mid-reasoning.
- An LLM-designed sampling strategy may be locally optimal (good at the current step)
  but globally suboptimal (missing a cheap cluster of options it didn't think to look at).
- For v1, the value of adaptive sampling over hierarchical sampling is marginal. Revisit
  if Phase 4 optimizer data shows systematic misses.

### Alternative 3: Multi-armed bandit (Thompson sampling)

Model each window as a bandit arm. After each observation (flight/hotel prices for a
window), update the posterior estimate of that window's utility. Pull arms with the
highest expected improvement.

**Rejected for v1 because:**
- Requires maintaining posterior distributions per window per route per season — a
  non-trivial statistical infrastructure.
- Cold-start problem: for new routes or new tenants, there is no prior data. The bandit
  degenerates to random exploration in the cold-start case, which is no better than the
  hierarchical sampler.
- Adds significant algorithmic complexity that is hard to debug in production when a
  tenant reports "why didn't it show me week 3 options?"
- Thompson sampling is a Phase 2+ candidate once we have real pick-rate data from the
  Pareto archetype selection to build priors from.

---

*Referenced plan.md sections: §4.1, §4.2, §5.1, §5.2, §5.3, §13.2*
