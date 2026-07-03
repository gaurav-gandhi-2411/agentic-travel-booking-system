# ADR-0027 — LLM Provider Fallback Chain (Groq → OpenRouter)

**Date:** 2026-07-04
**Status:** Accepted
**Phase:** Post-Wave-2 prerequisite unblock

---

## Context

Free Groq TPD (100k tokens/day, sliding window, unmeasurable remaining) has blocked
eval generation and demo prep 5+ times (Issue #15/#16). It also degrades the LIVE
product: a real user hitting `/search` when Groq's Llama 3.3 70B TPD is exhausted gets
a hard 429 ("Search failed"), not a graceful degradation. See spec.md for the full
requirements.

Two design points were escalated for review before any code was written:

1. **Candidate validation** — every non-Groq candidate must be validated against the
   REAL planner (`extract_travel_intent`) and optimizer
   (`generate_archetype_explanation`/`generate_archetype_comparisons`) tool schemas.
   GPT-OSS-120B was already ruled out for two schema incompatibilities (null for
   non-nullable numeric fields; U+2011 in tool-call string args). A fallback that
   produces malformed tool calls is worse than a clean error.
2. **Chain config shape** — how the fallback is expressed in `llm_routing.yaml`.

## Candidate validation (apps/api/scripts/validate_fallback_candidates.py)

Two rounds, run live against OpenRouter, against the real planner + optimizer tool
schemas (null-for-non-nullable check, unicode/U+2011 check, pattern/enum check):

| Model | Schema | Availability | Verdict |
|---|---|---|---|
| `google/gemma-4-31b-it:free` | PASS — 16/16 clean (two rounds) | 100% | **Selected** |
| `meta-llama/llama-3.3-70b-instruct:free` | INCONCLUSIVE (0 clean signal) | 0% (16/16 calls, all 429, oversubscribed on OpenRouter's free routing) | Dropped from the chain (see Decision) |
| `nvidia/nemotron-3-super-120b-a12b:free` | FAIL — leaks chain-of-thought as plain text instead of calling the tool | n/a | Ruled out |
| `qwen/qwen3-next-80b-a3b-instruct:free` | INCONCLUSIVE (all 429) | 0% | Ruled out (no positive signal) |
| `qwen/qwen3-coder:free` | INCONCLUSIVE (all 429) | 0% | Ruled out (no positive signal) |
| `google/gemma-4-26b-a4b-it:free` | PASS where served (4/4, 0 violations) | 67% | Not selected — strictly worse availability than the 31B sibling |
| `nvidia/nemotron-3-nano-30b-a3b:free` | FAIL — same reasoning-leak failure family as the 120B nemotron | n/a | Ruled out |
| `deepseek/deepseek-v4-flash` | not tested | n/a | No `:free` variant exists on OpenRouter (confirmed live via `GET /api/v1/models`) — not a free candidate |

No free model on OpenRouter today is both stronger than Gemma-4-31B *and* reliably
available — every stronger candidate tested is either oversubscribed (429-only,
zero signal) or tool-schema-incompatible.

## Decision

### Chain shape

```
Groq llama-3.3-70b-versatile (primary)
  → OpenRouter google/gemma-4-31b-it:free (only fallback hop)
  → structured error (AllProvidersExhaustedError)
```

**OpenRouter Llama-3.3-70B was evaluated as position #2 (same model as primary, for
output-quality parity) and dropped.** It scored 0/6 successful calls in both
validation rounds (100% 429, oversubscribed on OpenRouter's free routing) — it has
never once served a request in testing. Keeping it in the chain would add a
guaranteed-to-fail round-trip on every real fallback, which is pure added latency on
exactly the path that fires when the system is already degraded. If OpenRouter's
capacity for this model changes in the future, re-adding it as a genuinely fast-failing
hop (<1-2s, non-blocking) is a reasonable follow-up — not the default today.

### Config shape (`apps/api/config/llm_routing.yaml`)

A `fallback_chain` key on the profile, scoped per agent:

```yaml
demo-llama:
  planner:      llama-3.3-70b-versatile
  optimizer:    llama-3.3-70b-versatile
  conversation: llama-3.3-70b-versatile
  provider:     groq
  fallback_chain:
    planner:
      - provider: openrouter
        model: google/gemma-4-31b-it:free
    optimizer:
      - provider: openrouter
        model: google/gemma-4-31b-it:free
```

Only `planner` and `optimizer` are covered. **`conversation` (ConversationManagerAgent)
is intentionally excluded** — its tool schema (`ConversationManagerOutput`) was never
validated against Gemma-4-31B; the validation script only covers
`EXTRACT_TRAVEL_INTENT` and the two `GENERATE_ARCHETYPE_*` tools. Wiring an unvalidated
model into an unvalidated schema would violate the project's core rule ("every
candidate model validated against our real tool schemas before inclusion"). A Groq
outage still hard-fails `/refine` until conversation_manager's schema is separately
validated — a known, tracked gap, not an oversight.

### Wrapper (`apps/api/src/travel_agent/llm/fallback.py`)

`FallbackLLMClient` implements the `LLMClient` protocol and wraps a primary client
plus an ordered list of `FallbackHop` (provider, model, client). On `chat()`:

- Tries each hop in order. Hop 0's model comes from the `model` kwarg the caller
  already passes (`self._model` on `PlannerAgent`/`OptimizerAgent`) — **no changes
  needed to any agent's call site**; hops 1..N use their own fixed model from the
  chain config.
- `LLMError.retryable` (new field, set by `_openai_compat.py`'s exception
  classification: 429/5xx/timeout → `True`; 400 and other 4xx → `False`) gates the
  fallback decision. A non-retryable error re-raises immediately — it never falls
  back, since the same bad request would fail identically on every hop.
- Every attempt (success or failure) is logged via structlog
  (`llm_fallback_attempt_failed` / `llm_fallback_served`). A successful fallback also
  reaches Sentry as a `capture_message(level="warning")`; full-chain exhaustion
  reaches Sentry as a `capture_exception`. Fallbacks are observable, never silent.
- No per-hop retry loop — one attempt per hop, then move on. The chain design
  explicitly avoids adding latency on the already-degraded path (see the
  OpenRouter-Llama decision above).

### Wiring — single choke point

`get_llm_client_and_model(agent, profile_name, *, use_fallback=True)` in
`travel_agent.llm.__init__` is the one function that both the production API routes
(`search.py`, `refine.py`) and the Wave 2 eval runner already call to build agent
clients. Extending it to return a `FallbackLLMClient` when the profile declares a
chain for that agent — instead of adding a second call path — means production and
eval automatically share the exact same chain with zero duplicated wiring.

### Eval provider transparency

`RequestState.served_model: dict[str, str]` (new field) is populated by
`PlannerAgent`/`OptimizerAgent` with the actual `response.model` from whichever hop
served the call — this can differ from the routing profile's *configured* model when
a fallback fired mid-case. The Wave 2 runner records `served_model_planner`,
`served_model_optimizer`, and a computed `fallback_used` flag per case, and prints a
summary of any mixed-provider cases at the end of a run.

**The authoritative Wave 2 baseline must be generated with `--no-fallback`** (new CLI
flag, threading `use_fallback=False` through `get_llm_client_and_model`) so every case
is served by one model. The default (fallback ON) is for production resilience and
non-blocking eval reruns — never treated as the authoritative baseline. See
`evals/wave2/README.md` § Fallback and the authoritative baseline.

---

## Consequences

**Positive:**
- `/search` and `/refine` degrade gracefully under Groq TPD exhaustion instead of a
  hard 429, for the planner and optimizer calls (the majority of the pipeline).
- The Wave 2 eval runner can complete a full pass through a Groq TPD wall via the
  same chain, with per-case provider transparency so a mixed-provider run is never
  silently mistaken for a clean baseline.
- Zero agent-level code changes required for the fallback itself — `PlannerAgent` and
  `OptimizerAgent` are unaware they might be talking to a `FallbackLLMClient`.

**Negative / accepted:**
- `conversation_manager` (`/refine`'s classification step) has no fallback yet — a
  Groq outage still fails that path. Tracked as a follow-up; requires validating
  Gemma-4-31B (or another candidate) against `ConversationManagerOutput`'s schema
  first, per the project's non-negotiable validation rule.
- `google/gemma-4-31b-it:free` is a materially weaker model than Llama 3.3 70B. A
  fallback-served archetype explanation may read less specific/detailed than a
  Groq-served one — expected and flagged via `served_model`/`fallback_used`, not a bug.
- OpenRouter free tier (20 RPM, ~200-1000 req/day) is not production-grade capacity.
  If Gemma-4-31B itself becomes saturated under real fallback load, the chain still
  ends in a clean structured error — no infinite retry, no cascading failure — but
  that's a real ceiling. A paid OpenRouter tier (one-time top-up, raises daily caps
  permanently) is the documented next step if free-tier fallback capacity proves
  insufficient in practice; not spent as part of this ADR.

## Alternatives considered

**Keep OpenRouter Llama-3.3-70B at chain position #2** (same model as primary, for
output-quality parity, as originally specified). Rejected on validation evidence: 0/6
successful calls across two independent rounds testing means it would add a
guaranteed-to-fail hop — pure latency, zero benefit — on the exact path that only
fires when the system is already under stress.

**Per-hop retry before advancing to the next hop.** Rejected — the whole point of a
degraded-primary-provider path is to fail fast and move on, not to spend more time
retrying a hop that just failed.

**Cover `conversation_manager` in the initial chain by reusing the planner/optimizer
validation.** Rejected — `ConversationManagerOutput`'s schema was never exercised by
`validate_fallback_candidates.py`. Extending the fallback to an unvalidated schema
contradicts the project's explicit rule that every fallback candidate is validated
against the schema it will actually serve.
