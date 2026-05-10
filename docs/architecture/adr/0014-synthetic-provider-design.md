# ADR-0014: Synthetic Provider Design

**Status:** Accepted — 2026-05-11

---

## Context

With the provider stack revised (ADR-0013), several agent paths have no real data source:
`hotel_hunter` has no approved hotel program in v1, and `flight_hunter` needs deterministic
results for CI and gap-filling on niche routes where Aviasales has no cached data.

Three distinct use cases need a programmatic data source:

1. **CI tests** — tests must be deterministic, require no network calls, and produce
   results that exercise the full agent pipeline including the `OptimizerAgent`'s
   Pareto-frontier ranking (ADR-0006). Random or static fixture data does not scale to the
   full diversity matrix of route × budget × traveler-profile combinations.

2. **Gap-filling** — when Aviasales returns an empty result set for a niche route (e.g.,
   BBI→IXC, a domestic Indian route with limited cached data), the `flight_hunter` must
   still surface options rather than forcing the `CoordinatorAgent` to surface a "no results"
   failure to the user.

3. **Demo mode** — buyer presentations require controllable, plausible scenarios. The
   evaluator should not see obviously fake prices (₹100 flights) or obviously fake hotel
   names. Demo seeds should be varied enough to look realistic across multiple demo runs.

An ad hoc approach — separate mocks for CI, hard-coded fixtures for gap-filling, manual
data for demos — produces three code paths with diverging realism and maintenance burden.
A single, shared Synthetic provider eliminates the divergence.

---

## Decision

### `SyntheticFlightProvider` and `SyntheticHotelProvider`

Both implement the same `FlightProvider` and `HotelProvider` Protocols (ADR-0002) as the
real adapters. From the agent's perspective, calling `SyntheticFlightProvider.search()` is
identical to calling `AviasalesAdapter.search()`. The `synthetic_when_unavailable` routing
flag (ADR-0013) selects between them transparently.

### Generation rules

**Flights:**

- **Route realism:** Straight-line distance between airport pairs (from a bundled
  `airport_coords.json`) determines flight duration. Non-stop flights exist only for routes
  below 6,000 km with at least two daily frequencies in the real world (bundled
  `route_frequencies.json`). Longer or lower-frequency routes generate 1-stop itineraries
  via a plausible hub (DEL, DXB, LHR, SIN, AMS depending on region).

- **Airline-by-origin mapping:** Airline selection is weighted by realistic market share at
  the origin airport. India-origin routes weight IndiGo (40%), Air India (25%), Vistara
  (20%), others (15%). Europe-origin routes weight Lufthansa, BA, Air France, Ryanair,
  easyJet by destination type (LCC vs full-service). Middle East hub routes weight Emirates,
  Etihad, Qatar. Airline lists live in `airlines_by_region.json`.

- **Pricing:** Base price = distance_km × price_per_km_baseline × cabin_multiplier, where
  `price_per_km_baseline` is drawn from a per-route-type distribution in
  `price_baselines.json` (e.g., India domestic: ₹4–8/km, Europe short-haul: €0.06–0.15/km).
  A stochastic spread of ±30% is applied per option using the request seed, giving realistic
  price dispersion across 3–6 options per search.

- **Departure time distribution:** Early morning (05:00–07:00), morning (07:00–11:00),
  afternoon (12:00–17:00), evening (17:00–21:00), night (21:00–23:59). Frequency weights
  vary by route type (business routes skew AM/PM, leisure routes spread evenly).

**Hotels:**

- **Star-rating distribution by destination tier:** Tier-1 cities (Mumbai, London, NYC) skew
  toward 4–5 star; Tier-2 (Jaipur, Krakow, Porto) skew 3–4 star; Tier-3 (Hampi, Kotor)
  skew 2–3 star. Tier mappings in `destination_tiers.json`.

- **Hotel name and chain realism:** Names are drawn from a bundled list of plausible hotel
  brands per region (`hotel_chains_by_region.json`): Marriott/Hilton/IHG for global chains;
  OYO/Treebo/Zostel for India budget; Ibis/Mercure/Novotel for Europe mid-range. Names are
  composited as `"{chain} {destination_name} {qualifier}"` (e.g., "Ibis Porto Ribeira").

- **Review scores:** Drawn from a per-star-rating distribution (4-star: μ=8.3, σ=0.4 on a
  10-point scale) to avoid obviously uniform scores.

- **Nightly rates:** price_per_night_baseline × star_multiplier × ±25% spread, from
  `hotel_price_baselines.json`.

### Deterministic seeding

The Synthetic provider accepts a `seed: int` parameter sourced from `RequestState.request_id`
(a hash of the canonical query). The same query always returns the same synthetic options.
This property is essential for:
- Eval reproducibility: the eval harness (ADR-0010) can re-run the same query and get the
  same options without recording fixtures.
- CI stability: snapshot tests assert on specific output values without capturing network
  responses.
- Demo consistency: a demo script specifying `seed=42` always produces the same package for
  a rehearsed walkthrough; `seed=time.time()` gives variety in live demos.

### Source-of-realism bundled data

All generation rules reference bundled JSON files in
`apps/api/src/travel_agent/providers/synthetic/data/`:

| File | Contents |
|---|---|
| `airport_coords.json` | IATA code → lat/lon for ~3,000 airports |
| `route_frequencies.json` | Airport-pair → estimated daily frequency |
| `airlines_by_region.json` | Region → [(airline_code, name, market_share_weight)] |
| `price_baselines.json` | Route type → (min_price_per_km, max_price_per_km, currency) |
| `destination_tiers.json` | City/airport → tier (1/2/3) |
| `hotel_chains_by_region.json` | Region → [(chain_name, min_stars, max_stars)] |
| `hotel_price_baselines.json` | Star rating → (min_price_per_night, max_price_per_night) |

These files are curated by hand from public sources (Wikipedia airline market share data,
OAG frequency data, published hotel rate surveys). They are not generated by an LLM or
scraped. They will drift from reality over time but the drift rate is slow enough that
annual review is sufficient.

### Synthetic disclosure

Every option returned by the Synthetic provider includes:

```python
@dataclass
class FlightOption:
    ...
    provider: str          # always "synthetic"
    synthetic_disclosure: bool  # always True
```

The UI layer surfaces this as a disclosure badge ("Indicative pricing — book on partner
site for live fares"). The `BookingAgent` does not build an affiliate redirect for
synthetic-only results unless explicitly configured to do so per tenant.

---

## Consequences

**Positive:**
- Single code path for CI, gap-filling, and demos eliminates fixture maintenance burden.
- Seeded determinism gives the eval harness stable ground truth without recorded fixtures
  that go stale when real API schemas change.
- `hotel_hunter` is functional in v1 even without a real hotel provider. The system can
  be demoed end-to-end with full hotel package recommendations.
- Airline/hotel name realism passes visual inspection in buyer demos without being
  genuinely misleading (always disclosed as synthetic).

**Negative:**
- Bundled data files require periodic curation. Airlines change market share; price
  baselines drift with inflation. This is a maintenance cost, not a blocking issue.
- Seeded generation means two different queries with the same seed produce the same
  options even if they should differ. Callers must ensure `request_id` is unique per
  logical search (it is, because `RequestState.request_id` is derived from the full
  canonical query including dates, route, and traveler count).
- Synthetic hotel results in the optimizer output are clearly marked but still require
  the UI to handle the disclosure cleanly. A poorly-implemented UI could confuse users
  who do not notice the badge.

**Neutral:**
- The `SyntheticFlightProvider` and `SyntheticHotelProvider` are tested independently
  from real providers. Their test suite asserts on statistical properties (price spread
  within expected range, airline distribution within expected weights) not on exact values,
  since exact values are seed-dependent.
- The Synthetic provider is not a performance optimization. Its search() call is
  synchronous CPU-bound computation; for large option sets it may be slower than a cached
  API call. At v1 volume (3–6 options per search) this is irrelevant.

---

## Alternatives Considered

### Pure random generation (no bundled data, no rules)

Generate random prices, random airline names (e.g., UUID-seeded strings), random hotels.

**Rejected:** Random outputs are not realistic enough for demo use. More critically, the
eval harness judges compare two options using an LLM judge; implausible options (₹100 
flights, "Airline_7f3a" operating BOM→LHR) confuse the judge's preference scoring and
produce noisy eval results. Realistic generation is required for sound eval scoring.

### Static fixture files only

Pre-record a fixed set of (query → response) pairs as JSON fixtures. CI and gap-filling
use these fixtures verbatim.

**Rejected:** Does not scale to the diversity matrix (ADR-0011 seeds: 2,304 combinations
of route × traveler-profile × budget × ambiguity × trip-type). Recording a fixture for
each combination is impractical. A generative approach with realistic rules covers the full
matrix at zero fixture-recording cost.

### Recording real API responses as fixtures (VCR-style)

Use `vcrpy` to record real Aviasales API responses and replay them in CI.

**Rejected:**
- Stale-data problem: recorded prices are point-in-time. After 30 days they no longer
  reflect any real market signal, making the eval results meaningless.
- Copyright ambiguity: Travelpayouts's ToS is not clear on whether recorded API responses
  can be committed to a public repository. Given the open-source publishing intent
  (ADR-0012), bundling recorded responses creates a potential ToS violation.
- Schema brittleness: when Travelpayouts updates its API response schema, all recorded
  fixtures break simultaneously.

---

*Referenced plan.md sections: §9, §11, §17. Related: ADR-0002, ADR-0006, ADR-0010, ADR-0013.*
