# ADR-0013: Provider Stack Revision — Travelpayouts + Synthetic

**Status:** Accepted — 2026-05-11

---

## Context

The original provider stack assumed three data sources: Amadeus Self-Service API (flight
search and booking sandbox), Duffel (hotel and flight search with test-mode booking), and
Kiwi.com (flight search with deep-link redirect). All three have been eliminated:

- **Amadeus Self-Service** is being decommissioned on July 17, 2026. New integrations are
  not viable; existing integrations will break before Phase 1 ships.
- **Duffel** does not onboard India-based partners without formal business registration.
  The company is not yet incorporated, making Duffel inaccessible until an incorporation
  milestone that has no committed date.
- **Kiwi.com** closed self-serve developer signups. Access is now partner-application-only
  with no self-service path for early-stage projects.

The provider gap is not symmetric: flight pricing data is available through other channels,
but hotel real-data providers (Booking.com, Agoda, Expedia affiliates) require a credible
public website before approving applications — which does not yet exist.

This ADR records the replacement provider strategy and the routing flag that manages
the transition from placeholder to real providers per agent as approvals land.

---

## Decision

### Primary flight pricing provider: Travelpayouts Aviasales Data API

Travelpayouts's Aviasales Data API provides cached flight pricing data across a wide route
network. It is:
- Free (no per-call charges; revenue-share model via affiliate markers)
- India-accepted (no business registration required; individual developer accounts approved)
- Already onboarded (token obtained)

The Aviasales Data API returns cached pricing from recent searches, not live GDS inventory.
This is an intentional and acceptable trade-off: the window-search algorithm (ADR-0005)
reasons over price *trends* across a 30-day horizon, not millisecond-accurate availability.
A cached price signal is sufficient for window optimization and two-archetype ranking. Live
availability and exact pricing are resolved at the affiliate redirect step, where the user
lands on the airline or OTA's own booking page. We do not promise an exact locked fare —
we promise a well-reasoned package recommendation.

Travelpayouts is the **sole real provider** for flight pricing in v1. Additional
Travelpayouts programs (hotels via Hotellook, activities via Klook, etc.) are added
incrementally as program applications are approved. The program approval sequence is tracked
in `docs/backlog.md`.

### Secondary provider for all agents: Synthetic provider

A `SyntheticFlightProvider` and `SyntheticHotelProvider` (specified in ADR-0014) implement
the same `FlightProvider` and `HotelProvider` Protocols (ADR-0002) as the real adapters.
The Synthetic provider serves three purposes:

1. **CI tests** — deterministic, no network calls, reproducible fixture generation
2. **Gap-filling** — when Aviasales has no cached data for a niche route, or when a real
   hotel provider is not yet approved, the Synthetic provider returns plausible options
   rather than an empty result set
3. **Demo mode** — parametric scenario generation for buyer presentations with controllable
   price points, route patterns, and hotel tiers

### Routing flag: `synthetic_when_unavailable`

The provider routing config gains a boolean flag `synthetic_when_unavailable` per agent.
When `true`, the agent falls back to the Synthetic provider if the configured real provider
returns an empty result set or is not configured. When `false`, an empty result propagates
to the coordinator as-is (appropriate for booking-critical paths where synthetic data would
be misleading).

Default values:

| Agent | `synthetic_when_unavailable` | Rationale |
|---|---|---|
| `flight_hunter` | `true` | Gap-fill niche routes; demo support |
| `hotel_hunter` | `true` | No real hotel provider in v1; Synthetic is the primary |
| `optimizer` | `false` | Optimizer ranks what agents surface; no direct provider call |
| `planner` | `false` | Planner does not call providers directly |
| `booking` | `false` | Synthetic booking data would be misleading in the HITL flow |
| `conversation` | `false` | Conversation agent does not call providers |

### Hotel real-data providers: deferred

No hotel program has been approved. `hotel_hunter` runs on the Synthetic provider
exclusively in v1. Booking.com, Agoda, Hotellook, and Trip.com applications are gated
on a credible public website (Phase 0.5). Approval timelines are tracked in
`docs/backlog.md`.

---

## Consequences

**Positive:**
- Unblocks Phase 1 immediately. No waiting for provider approvals, incorporation, or
  decommissioning cliffs.
- Travelpayouts is already onboarded; the token is in hand. Zero additional signup friction.
- The `synthetic_when_unavailable` flag keeps agent behavior deterministic in CI without
  special-casing test environments in application code.
- Cached pricing is sufficient for the core value proposition: window optimization and
  two-archetype ranking reason over trends, not ticks.

**Negative:**
- Hotel real-data is absent in v1. `hotel_hunter` returns synthetic results, which must be
  clearly disclosed to the user (handled at the UI layer and by the `synthetic_disclosure`
  flag on each option).
- Aviasales cached data may lag real-market pricing. The agent's recommendations are
  directionally correct, not executable fares. The affiliate redirect step closes this gap
  by landing the user on a live booking page.
- Travelpayouts affiliate commission reporting has a 30–60 day lag, identical to the
  affiliate model noted in ADR-0003.

**Neutral:**
- The `FlightProvider` and `HotelProvider` Protocol interfaces (ADR-0002) are unchanged.
  Swapping in Aviasales is an adapter implementation detail, not an interface change.
- The `BookingAgent` is unaffected: it calls `AffiliateRedirectBuilder`, which builds
  a Travelpayouts deep-link URL. The link structure differs from Amadeus/Duffel but the
  HITL state machine is identical (ADR-0003 amendment).

---

## Alternatives Considered

### FlightAPI.io

Paid subscription ($49+/month). No free tier.

**Rejected:** $0 API spend is a hard constraint (plan.md §15). Any paid flight data API
violates the cost model at v1 volume.

### AviationStack

Provides flight status and schedule data — not pricing data.

**Rejected:** Window-search optimization requires price signals across a 30-day horizon.
AviationStack does not provide pricing; it cannot substitute for Aviasales.

### RapidAPI scraper-based flight APIs

Several RapidAPI endpoints aggregate pricing data via browser-automation scraping.

**Rejected:** These APIs violate the ToS of the underlying airlines and OTAs they scrape.
Using them creates legal exposure. They are also brittle — scraping-based APIs break when
the target site changes its markup, making them unreliable for an eval harness that needs
stable data.

### Pure Synthetic for v1 (no real provider)

Skip Travelpayouts entirely. Use only the Synthetic provider for all agents in Phase 1.

**Rejected:** Undermines the core credibility claim. A buyer evaluating the system needs to
see real price signals flowing through the window-search algorithm, not parametric
placeholders. The Aviasales Data API removes this objection at zero cost.

---

*Referenced plan.md sections: §9, §11, §17. Related: ADR-0002, ADR-0003, ADR-0014.*
