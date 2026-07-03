# Project Spec: DealHunter — LLM Provider Fallback Chain

## Strategic context
The free Groq TPD ceiling (100k/day, sliding window, unmeasurable remaining) has
blocked work 6+ times — demo prep, smokes, and now the Wave 2 baseline repeatedly.
It also degrades the LIVE product: a real user hitting /demo when Llama's TPD is
exhausted gets a hard 429 ("Search failed"). A provider fallback chain fixes BOTH:
production search degrades gracefully instead of erroring, and eval generation runs
through TPD walls automatically.

This is a prerequisite-unblock (do this BEFORE the Wave 2 baseline run) AND a real
production-resilience feature.

## Goal
A resilient LLM-call layer: when the primary provider/model returns a rate-limit
(429) or transient error, automatically fall back to the next provider in an ordered
chain — same model on a different provider first (to preserve output quality), then
other free models — before surfacing an error. Used by BOTH the production search
path and eval generation (one chain, configured once).

## Fallback order (DECIDED)
1. **Groq llama-3.3-70b-versatile** (primary — current prod planner)
2. **OpenRouter Llama-3.3-70B** (SAME model, different provider/bucket — near-identical
   output, separate rate limit)
3. **Other OpenRouter free model(s)** — orchestrator researches which free OpenRouter
   models satisfy our tool-schema needs (function calling + the planner/optimizer tool
   contracts) and proposes the specific model(s). MUST handle our tool schema.
4. **Error** (only after the whole chain is exhausted) — a clear, structured error.

### Critical compatibility constraint (LEARNED)
GPT-OSS-120B is RULED OUT as a fallback — confirmed two schema incompatibilities:
returns null for non-nullable numeric fields (Groq 400) and emits U+2011 in tool
args (JSON parse fail). ANY candidate fallback model MUST be validated against our
ACTUAL tool schemas (planner + optimizer) before inclusion — same null-field +
unicode + tool-call-format checks. A fallback that produces malformed tool calls is
worse than a clean error. Validate each candidate before adding it to the chain.

## Current state
- LLM calls route via llm_routing.yaml profiles (demo-llama = Groq Llama;
  demo-gpt-oss; demo-haiku=Anthropic placeholder). Routing layer in routing.py /
  the llm adapters.
- Groq free: 100k TPD shared across planner+optimizer+refine, sliding 24h window,
  no remaining-quota in response headers (can't pre-check).
- OpenRouter: the project already has OPENROUTER_API_KEY as a prod secret (it's in
  deploy-prod.yml secrets). Confirm it's valid / has free-model access.

### Load-bearing — handle with care
- llm_routing.yaml + routing.py (the change extends routing — escalate the design).
- Tool schemas (planner, optimizer) — do NOT change them; the fallback must satisfy
  them as-is.
- No ANTHROPIC_API_KEY ever. The chain is Groq + OpenRouter free only.

## Scope

### 1. Fallback-capable LLM call wrapper
- A wrapper around the LLM call that, on 429 (and configurable transient errors:
  timeouts, 5xx), tries the next provider/model in the chain. On success, returns
  normally. On full-chain exhaustion, raises a clear structured error.
- Distinguish RETRYABLE (429, timeout, 5xx) from NON-retryable (400 bad request,
  malformed schema) — don't fall back on a 400 caused by our own bad request; do
  fall back on rate-limit/transient.
- Per-attempt logging (structlog + the Sentry path from Wave 1): which provider was
  tried, why it fell back, which one served. So a fallback is OBSERVABLE, not silent.
- Preserve the tool-call contract across providers (same tools, same expected
  response shape). Normalize provider response differences if any.

### 2. Provider/model config
- Express the chain in config (extend llm_routing.yaml or a new fallback config) so
  the order is declarative and per-profile. Show me the config shape.
- OpenRouter adapter: confirm/extend the LLM client to call OpenRouter (OpenAI-compat
  API) for Llama-3.3-70B + the chosen free model(s), using OPENROUTER_API_KEY.

### 3. Candidate validation (BEFORE wiring into the chain)
- For each non-Groq candidate (OpenRouter Llama-3.3-70B + any free model): run our
  ACTUAL planner + optimizer tool calls against it and confirm: valid tool-call JSON,
  no null-for-non-nullable, no unicode/U+2011 breakage, correct field types. Only
  models that PASS go in the chain. Report the validation per candidate.

### 4. Apply to both paths
- Production search path (planner/optimizer/refine) uses the chain — so live /demo
  degrades gracefully instead of 429-ing.
- Eval generation (runner.py) uses the SAME chain — so the baseline run completes
  through TPD walls.

### 5. Eval impact note
- Record, per eval case, WHICH provider/model actually served it (Groq vs OpenRouter
  fallback), so a baseline that mixed providers is transparent. Same-model fallback
  (OpenRouter Llama-3.3-70B) keeps quality comparable; a drop to a different free
  model should be flagged in that case's record so we know the output may differ.

## Verification
```yaml
- name: fallback_on_429
  cmd: "simulate/force a Groq 429 -> confirm the call falls back to OpenRouter Llama and returns valid output (not an error)"
  required: true
- name: no_fallback_on_400
  cmd: "a genuine bad-request 400 does NOT trigger fallback (surfaces the real error)"
  required: true
- name: candidate_schema_valid
  cmd: "each non-Groq chain model passes the planner + optimizer tool-call validation (no null-field/unicode/format breakage)"
  required: true
- name: observable
  cmd: "a fallback event is logged (structlog + Sentry) with from/to provider + reason"
  required: true
- name: eval_completes
  cmd: "the eval runner completes a full pass even when Groq TPD is exhausted (via fallback)"
  required: true
- name: tests
  cmd: "pytest -q && existing tests green"
  required: true
```

## Escalation rules
- Show me: (a) the candidate-model VALIDATION results before wiring any model into
  the chain, and (b) the fallback CONFIG shape (routing.yaml extension) before
  building the wrapper. These are the two design points.
- llm_routing.yaml / routing.py are load-bearing — show the diff approach.
- Any prod deploy (this touches the prod search path) = backend canary->full,
  GG-gated, with a smoke that forces a fallback and confirms graceful degradation.
- No ANTHROPIC_API_KEY. Chain = Groq + OpenRouter free only.

## Hard rules
- Fall back on retryable errors (429/timeout/5xx) only; never on 400/our-own-bad-request.
- Every candidate model validated against our real tool schemas before inclusion.
- Fallbacks are OBSERVABLE (logged + Sentry), never silent.
- No change to tool schemas; the chain satisfies them as-is.
- Eval records which provider served each case (provider transparency).

## Success criteria
- A forced Groq 429 transparently falls back to OpenRouter Llama-3.3-70B and returns
  valid tool-call output; full-chain exhaustion gives a clean structured error.
- 400s do NOT fall back. Fallbacks are logged + in Sentry.
- Every chain model validated against planner+optimizer tool schemas.
- Live /demo search degrades gracefully under Groq TPD exhaustion (no hard 429 to user).
- The Wave 2 eval runner completes a FULL 31-case pass despite Groq TPD limits, with
  per-case provider recorded.
- Tests green.

## Build order
1. Confirm OPENROUTER_API_KEY is valid + has free-model access. Research + propose
   the chain models (OpenRouter Llama-3.3-70B + free candidate[s]).
2. VALIDATE each candidate against our real planner + optimizer tool calls
   (null-field / unicode / tool-format). Report — only passing models go in chain.
   STOP for my review of the chain.
3. Show me the fallback CONFIG shape (routing.yaml extension). STOP for approval.
4. Build the fallback wrapper (retryable-vs-not, per-attempt logging/Sentry,
   contract preservation).
5. Wire into production search path + eval runner (same chain).
6. Tests (forced 429 -> fallback; 400 -> no fallback; observability).
7. Deploy prod (canary->full, GG-gated) with a fallback-forcing smoke.
8. THEN: run the full Wave 2 Tier-1 baseline through the chain (no longer TPD-blocked).
```
