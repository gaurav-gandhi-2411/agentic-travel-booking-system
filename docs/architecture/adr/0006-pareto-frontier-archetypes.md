# ADR-0006: Pareto Frontier Archetypes (Best-Value / Best-Experience)

**Status:** Accepted — 2026-05-09

---

## Context

After `WindowSearcher` collects `FlightOption[]` and `HotelOption[]` across candidate
windows, `OptimizerAgent` must present the results to the user. A typical run produces
50–200 flight × hotel × window combinations. The user cannot evaluate all of them.

The presentation design question is: **how many options do we show, and how do we
choose them?**

This decision shapes the entire user experience. Travel is a high-stakes purchase —
users are anxious about making the wrong choice. The system must reduce decision fatigue
while still feeling like it searched comprehensively and not just gave the user the
first result it found.

The competitive context matters: every OTA (Kayak, Skyscanner, MakeMyTrip) already
produces a ranked list of cheapest-to-most-expensive results. If we produce the same
thing, the agent layer adds no discernible value over existing tools. The value must be
visible in the presentation, not just in the search breadth.

Additional requirements:
- `OptimizerAgent` must be able to explain its choices in 2–3 natural-language sentences.
  These explanations are a primary product differentiator — they give users the "why"
  behind the recommendation.
- When a user asks "show me cheaper", the refinement path must be well-defined: the
  user has expressed a preference shift along the value dimension, which should move
  them toward a different point on the frontier, not trigger a full new search.
- The two archetypes must be **meaningfully different** from each other. If "best-value"
  and "best-experience" end up being adjacent entries in a sorted price list, the
  archetype framing provides no value.

---

## Decision

We present exactly **two archetypes** extracted from the **Pareto frontier** of the
value and experience utility dimensions.

### The two utility functions (summary — full spec in plan.md §6.1, §6.2)

```
value_score = w1 * (1/total_cost)
            + w2 * (flight_quality_score)    # layovers, duration, departure time
            + w3 * (hotel_value_score)        # stars/price ratio vs market p25
            + w4 * (window_desirability)      # peak vs shoulder, weekend coverage

experience_score = w1' * (flight_comfort_score)   # cabin, airline reputation
                 + w2' * (hotel_rating × review)
                 + w3' * (location_centrality)
                 + w4' * (refundability_score)     # 0=non-refundable, 1=fully flexible
                 - penalty(total_cost > p75)
```

The two scores are independent: a high experience_score does not require a low
value_score (a refundable direct flight at a well-reviewed boutique hotel near the
city center can score well on both). This is intentional — the Pareto frontier is
the set of options where no other option is strictly better on both dimensions.

### Pareto frontier extraction

`apps/api/src/travel_agent/scoring/pareto.py` computes the 2D Pareto frontier
across all `(value_score, experience_score)` pairs. A package `P` is on the frontier
if no other package `Q` satisfies both `Q.value_score > P.value_score` AND
`Q.experience_score > P.experience_score`.

From the frontier:
- **Best-value archetype:** the frontier package with the highest `value_score`.
- **Best-experience archetype:** the frontier package with the highest `experience_score`.

If these are the same package (it dominates on both dimensions), we select the
second-ranked experience package as the experience archetype. This guarantees the
user always sees two distinct options.

### What "meaningfully different" means in practice

Consider a route where two frontier packages are:

| Package | Value score | Experience score | Total cost | Hotel rating | Flight stops |
|---------|-------------|-----------------|------------|--------------|--------------|
| A       | 0.84        | 0.51            | ₹52,400    | 3.8★         | 1 stop       |
| B       | 0.62        | 0.88            | ₹71,200    | 4.7★         | Direct       |

Package A wins on value (cheaper, acceptable hotel). Package B wins on experience
(premium hotel, direct flight). The ₹18,800 difference is meaningful — it is both the
cost of the upgrade and the explanation: *"The experience package costs ₹18,800 more
for a direct flight and a 4.7-star hotel. The value package gets you there with one stop
and a solid 3.8-star option at ₹52,400 total."*

The Pareto condition guarantees this meaningful difference: if A were better on both
dimensions, B would not be on the frontier.

### Natural-language explanation generation

`OptimizerAgent` (Sonnet) generates 2–3 sentences per archetype that translate the
utility scores into human language. The prompt provides:
- The winning package's key attributes (flight details, hotel name, dates, total cost)
- The delta vs. the other archetype (cost difference, key trade-offs)
- The scoring weights (so it can say "saves ₹18k by avoiding peak-weekend departure")

These explanations are the primary output the user sees — not score tables.

### Refinement path integration

When the user says "show me cheaper":
- `ConversationManagerAgent` interprets this as a `value_preference_increase` signal.
- The coordinator re-runs `OptimizerAgent` with a mutated weight vector that increases
  `w1` (cost weight) in `value_score` and adds a `total_cost_ceiling` constraint.
- No new provider calls are made — the previously collected `FlightOption[]` and
  `HotelOption[]` are reused.
- `OptimizerAgent` re-extracts the Pareto frontier under the new weights and returns
  two new archetypes.

This is the key reason the Pareto frontier approach works for refinement: the
solution space is already materialized in `RequestState`. Refinement is a re-scoring
and re-extraction on existing data, not a new search.

---

## Consequences

**Positive:**
- The Pareto condition guarantees the two archetypes are meaningfully different. This
  is a mathematical property, not an engineering heuristic. If they look similar, it
  means the user's specific request genuinely has a tight Pareto frontier (common on
  short-haul routes with limited options), not a bug in the selection logic.
- Two choices with clear trade-offs (cost vs. quality) match the actual decision
  framework most travelers use. "Do I want to spend more for a better experience?"
  is the core question; the archetypes make it answerable.
- The natural-language explanation per archetype is the product differentiator. A
  ranked list of 20 options cannot generate "saves ₹18k by avoiding a peak-weekend
  departure" — that requires the comparative reasoning the optimizer performs.
- The refinement path (re-score existing candidates, no new search) keeps refinement
  latency in the 1–2s range (one OptimizerAgent call, no provider calls).
- Per-tenant scoring weight overrides (plan.md §8.1 `ScoringWeights`) allow tenants to
  tune the archetypes for their user base (e.g., a business travel platform may weight
  refundability more heavily in `experience_score`). The Pareto approach accommodates
  this: different weights → different frontier → different archetypes.

**Negative:**
- Two options can feel limiting for users who want to browse. The "refine" path
  (conversational loop) is the intended browsing mechanism, but it requires users to
  articulate their preferences in natural language rather than scanning a grid.
- The Pareto frontier can collapse to one point on very constrained routes (e.g.,
  a single daily flight to a small city with one hotel option). The "second-ranked
  experience" fallback handles this, but the user sees two options that are nearly
  identical in this case.
- Utility function weights are initially hand-tuned. Wrong weights produce archetypes
  that don't match what users actually prefer. The v2 candidate (learning weights from
  pick-rate data) requires significant data collection before it's viable.
- The `experience_score` penalty for `total_cost > p75` is a heuristic that prevents
  the experience archetype from being an unaffordable outlier. The p75 threshold is
  computed per route per travel period, requiring a baseline market price distribution.
  Building this baseline requires provider API calls beyond the search itself.

**Neutral:**
- The Pareto frontier is a 2D construct here (value, experience). Adding a third
  dimension (e.g., sustainability score) in Phase 2 changes the frontier computation
  from O(n²) 2D dominance to O(n² × d) d-dimensional dominance, and the two-archetype
  presentation model must be reconsidered. This is a deliberate trade-off of v1
  simplicity for Phase 2 complexity.
- `OptimizerAgent` is the only agent that sees the full candidate set. All other agents
  operate on bounded windows. This concentration of the scoring/ranking logic in one
  agent is intentional: it keeps the scoring model in one place, versionable, and
  auditable.

---

## Alternatives Considered

### Alternative 1: Single ranked list

Show the top-10 (or top-N) packages ranked by a single composite score. The user
scrolls and picks.

**Rejected because:**
- This is exactly what every existing OTA produces. A single ranking provides no
  differentiation from Skyscanner's "cheapest" sort or Google Flights' default.
- A composite score that blends value and experience cannot be explained in plain
  language without sounding like marketing ("this is our recommended option"). The
  Pareto + NL explanation approach produces an honest explanation of the trade-offs.
- A ranked list of 10 items produces decision paralysis for travel purchases. Research
  on choice architecture (Iyengar & Lepper) shows that fewer, clearly differentiated
  choices produce higher conversion and satisfaction.
- The composite score weights are arbitrary to the user — "ranked by our proprietary
  score" is not a satisfying explanation.

### Alternative 2: Three archetypes — cheapest, best-value, best-experience

Add a "cheapest" archetype (pure price minimization) alongside value and experience.

**Rejected because:**
- "Cheapest" and "best-value" frequently collapse to the same package. A cheap flight
  + cheap hotel can be the value winner if the ratio of quality to price is good.
  Showing two nearly identical options in the cheapest and value slots confuses users.
- A three-archetype presentation requires a wider UI layout and more explanation text,
  increasing cognitive load.
- "Cheapest" is already the dominant presentation mode of all OTAs. Leading with it
  positions the product as a price comparison tool, not a reasoning agent.
- If a user wants the absolute cheapest option, "show me cheaper" refinement converges
  there. The explicit refinement path is cleaner than a static third archetype.

### Alternative 3: N user-tunable archetypes

Let users define their own scoring dimensions (e.g., sliders for "price vs. comfort
vs. flexibility") and show the optimal package per archetype configuration.

**Rejected because:**
- Contradicts the NL-first design philosophy. The system is designed to accept "Book a
  flight to Rome" and produce a recommendation, not to require users to configure
  scoring weights.
- Users who need to tune scoring dimensions are the same users who already use Kayak's
  price grid and matrix filters — not the target user for a conversational agent.
- Per-tenant `ScoringWeights` (plan.md §8.1) achieves the "tunable archetypes" goal for
  the B2B buyer (who can set defaults for their user base), without exposing tuning UI
  to end users.
- N archetypes requires N explanations, N layout slots, and N refinement paths. The
  engineering cost scales non-linearly with N while user value scales sub-linearly.

---

*Referenced plan.md sections: §6.1, §6.2, §6.3, §6.4, §8.1*
