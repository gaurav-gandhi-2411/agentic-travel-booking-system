# ADR-0019: ConversationManagerAgent — Level 2 Intent Classification

**Status:** Accepted — 2026-05-19

---

## Context

The `/refine` endpoint shipped in Phase 2B with keyword-pattern dispatch: three
hardcoded strings (`cheaper`, `skip_red_eyes`, `non_stop`) matched against the raw
refinement text. Any input that did not match fell back to a full re-search. This was
deliberately minimal — sufficient to prove the refine loop works end-to-end without
adding LLM latency to the hot path.

Phase 2C.4 replaces the keyword matcher with a structured classification agent. The
motivation is fourfold:

1. **Coverage.** Keyword matching handles three refinement types. Users express intent
   in unlimited ways: "under ₹20k", "morning only", "actually try Tokyo", "what about
   July instead". A classifier handles the full space; a keyword list cannot.

2. **Precision.** "Direct flights please" does not contain any keyword in the current
   list. "Cheap and direct" matches `cheaper` but silently drops the direct-only
   constraint. LLM classification extracts structured args correctly.

3. **Dispatch clarity.** REFINE (filter/sort cached pool) vs REPLAN (new search with
   modified intent) vs NO_OP (off-topic redirect) are meaningfully different code paths.
   Making the classifier explicit and testable makes the dispatch auditable.

4. **Eval gate.** An LLM classifier needs a quantitative acceptance threshold before it
   replaces the deterministic keyword matcher. This ADR establishes the gate and
   documents why the chosen approach passes it.

---

## Decision

Implement `ConversationManagerAgent` as a single-turn LLM classifier (Level 2) using
the same tool-use pattern as `PlannerAgent`: one LLM call with a forced JSON Schema
tool, structured args parsed by Pydantic, deterministic fallback on parse failure.

### Action taxonomy

Three mutually exclusive action types:

| Action | Meaning | Code path (PR 2) |
|--------|---------|------------------|
| `REFINE` | Filter or sort the existing cached flight pool. No new provider calls. | Apply `RefineArgs` to cached `FlightOption` list, re-run `OptimizerAgent`. |
| `REPLAN` | Start a new search with modified `TravelIntent`. | Merge `ReplanArgs` into current intent, re-run full pipeline via `stream_search`. |
| `NO_OP` | Off-topic input. Acknowledge and redirect. | Return polite redirect SSE event; do not touch cache or run any agent. |

### Args schema

**REFINE:**
```
price_max_inr: int | None       — upper price bound
price_min_inr: int | None       — lower price bound (rare but valid)
direct_only: bool               — exclude flights with layovers
max_layover_count: int | None   — allow at most N layovers
departure_window: morning | afternoon | evening | night | None
sort_by: price | duration | stops  — default "price"
clear_filters: bool             — reset all active filters to none
```

**REPLAN:**
```
origin_iata: str | None (3-char)
destination_iata: str | None (3-char)
departure_window_start: date | None
departure_window_end: date | None
flexible_dates: bool | None
preferred_airlines: list[str] | None
budget_max_inr: int | None
```
Null fields inherit from the current `TravelIntent` stored in `SearchCache`. The caller
(PR 2 route handler) is responsible for the merge.

**NO_OP:**
```
explanation: str (20–200 chars)
```
Polite redirect that acknowledges the input and refocuses on flight refinement. No
attempt to answer the off-topic question.

### Exactly-one-args invariant

`ConversationManagerOutput` enforces via `@model_validator(mode="after")` that exactly
one of `refine_args`, `replan_args`, `no_op_args` is populated. This makes the response
unambiguous to dispatch logic and eliminates a class of LLM hallucinations (e.g.
returning REFINE action with REPLAN args).

### Context available to the agent

The agent receives:
- Current `TravelIntent` (origin, destination, date window, budget)
- Aggregated flight pool stats (count, price range, stops range)
- Previously selected archetypes (label + price + stops — enough to frame "cheaper")
- The user's new message

It does **not** receive:
- Prior `/refine` messages or actions (no turn history)
- Full `FlightOption` records (not needed for classification)
- Any user preference history from prior sessions

The single-turn constraint is a deliberate design decision (see Alternatives section).

### Default profile

`demo-llama` (Llama 3.3 70B via Groq free tier).

**Evidence:**
- Phase 2C.1 planner baseline: Llama achieved 100% label-correct `extract_travel_intent`
  calls across all 24 eval scenarios — same accuracy as Haiku, at $0 cost.
- Intent classification is structurally similar to intent parsing: one tool call,
  structured JSON output, deterministic at temperature 0.0.
- Groq free-tier latency p50 for Llama is ~800ms — acceptable for interactive refinement
  (target p95 ≤ 4000ms per `thresholds.py`).
- Cost: $0 (Groq free tier, daily TPM/RPM reset).

The default is revisable after the cross-profile eval in S6. If another profile
materially outperforms Llama (>5pp on action accuracy), that profile becomes the
default and this ADR is amended.

### Fallback on tool-call failure

If the LLM returns no tool call or the tool output fails Pydantic validation:
- Return `ConversationManagerOutput(action=NO_OP, no_op_args=NoOpArgs(explanation="..."))` with a fixed
  fallback explanation string.
- Log a structured warning with the raw response content.
- Never surface a Python exception to the caller.

This mirrors `PlannerAgent`'s hard-error-on-failure pattern but inverts the failure
mode: PlannerAgent propagates ERROR phase because a failed parse is unrecoverable
(no intent = no search). ConversationManagerAgent silently degrades to NO_OP because
a failed classification can always be rephrased by the user.

### Telemetry

LLM call wrapped in a Langfuse span named `conversation_manager_classify`. Captured
fields: input message, output action+args (redacted if NO_OP explanation is PII-adjacent),
latency_ms, input/output tokens, cost_usd. Pattern identical to `planner_chat` span
in `PlannerAgent`.

---

## Eval gate

Cross-profile eval across 15 hand-curated scenarios (5 REFINE, 5 REPLAN, 5 NO_OP).
Each scenario run once per profile; NO_OP explanations scored by `eval-judge-qwen3-32b`
with median-of-3 sampling.

| Metric | Gate |
|--------|------|
| Action classification accuracy | ≥ 90% per profile (≥ 14/15 scenarios) |
| NO_OP coherence (judge score) | ≥ 4.0 avg across NO_OP scenarios |
| Latency p95 per profile | ≤ 4000ms |

The 90% accuracy gate is set at 14/15 — one scenario can be genuinely ambiguous
without failing the gate. All three profiles must clear the gate for the default to
be confirmed; failing profiles are documented as incompatible with
`ConversationManagerAgent` and excluded from the allowed-profiles list in PR 2.

---

## Consequences

**Positive:**
- Refinement covers the full user input space, not three keywords.
- REFINE/REPLAN/NO_OP dispatch is auditable and testable.
- Zero cost at default profile (Groq free tier).
- Eval gate gives a quantitative acceptance criterion before replacing the keyword matcher.

**Negative / trade-offs:**
- Adds one LLM call (~800ms p50) to the `/refine` hot path. Acceptable: the
  old keyword matcher was instant but handled only 3 intents; users who need intent
  classification are already tolerating SSE latency.
- Single-turn constraint means ambiguous inputs are classified into the closest action
  rather than triggering a clarifying question. See Alternatives.

---

## Alternatives

### Level 3: persistent multi-turn conversation memory

Add conversation history (prior user messages + agent actions) to each call. Store
in Redis alongside `SearchCache`.

**Rejected for Phase 2C.4 because:**
- Substantially increases prompt size on every call (previous turns grow unboundedly).
- Makes eval harness harder: each scenario is no longer a single-turn unit test —
  it needs a conversation fixture, not just a `(context, message)` pair.
- Per the locked decisions, no new persistence tables in Phase 2C. Redis schema
  is stable; adding turn history would require a new key structure or a `TurnHistory`
  model alongside `SearchCache`.
- The Level 2 trade-off (no clarifying questions, ambiguous inputs classified into
  closest action) is acceptable: users can rephrase. For a portfolio demo the marginal
  UX improvement of Level 3 does not justify the complexity.

Level 3 is deferred to Phase 2D or later.

### Tool-use schema hand-written (not Pydantic-derived)

Maintaining a parallel JSON Schema alongside the Pydantic models is error-prone.
`BaseModel.model_json_schema()` generates a correct schema from the model definition,
kept in sync automatically. The hand-written alternative was used for early prototyping
only.

### Keyword matcher extended with more patterns

Adding more keyword lists (e.g. destination patterns, month names) would expand the
current `/refine` coverage but would not scale to arbitrary user input and would not
produce structured args (e.g. `price_max_inr`). The LLM classifier supersedes the
keyword list; extending the list further is not worth the maintenance cost.

---

## References

- ADR-0001: Multi-agent coordinator pattern
- ADR-0008: Multi-provider LLM abstraction
- ADR-0009: Open-source model strategy
- ADR-0010: Eval harness design
- ADR-0015: Optimizer eval design
- ADR-0016: LLM judge design
- Phase 2C.1 eval baseline: `evals/optimizer/reports/` (Llama 100% label accuracy,
  coherence 4.583)
