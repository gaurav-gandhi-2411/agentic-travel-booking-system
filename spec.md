# Project Spec: DealHunter — Wave 2 (Eval Harness)

## Strategic context
Wave 1 (stabilize) is shipped + live: reliable booking, Sentry observability, honest
inventory. Wave 2 builds the AI MEASUREMENT layer — the instrument panel that makes
"push applied-AI as far as it goes" (Wave 3) possible, lets us prove quality to
customers, and is the strongest applied-AI signal in the project.

3-wave plan: Wave 1 stabilize (DONE) -> **Wave 2 evals (THIS)** -> Wave 3 AI depth
(measured against Wave 2).

## Goal
A reproducible eval harness that scores the agent across three behaviors, using the
RIGHT scorer per scope, runnable on demand, producing a quality report you can track
over time and show a customer/interviewer.

## Scoring architecture (DECIDED)
Right-scorer-per-scope — NOT LLM-judge-everything:
- **Planner extraction** -> DETERMINISTIC. There's a ground truth (origin/dest/
  dates/cabin/trip-type). Assert structured-field match. No LLM needed.
- **Refinement correctness** -> DETERMINISTIC/PROGRAMMATIC. The constraint is
  checkable: "make it cheaper" -> new results' prices < previous; "morning only" ->
  all results depart in the morning window; "direct only" -> 0 stops. Assert the
  constraint held in the output.
- **Recommendation / explanation quality** -> LLM-JUDGE (subjective). Is best-value
  defensible? Is the explanation accurate, relevant, non-generic? Graded by an LLM
  judge against a rubric.

## Judge (DECIDED)
- LLM judge runs **locally on Ollama** — zero API cost (off the Groq TPD budget),
  and a DIFFERENT model family than the Groq generator (no self-grading bias).
- Pin a specific Ollama judge model (e.g. llama3.1:8b or qwen2.5 — orchestrator
  recommends; must be a reasonable judge that runs on a typical dev machine).
- Judge prompt: structured rubric, returns a SCORE (e.g. 1-5 per criterion) + a
  short rationale, as parseable JSON. Deterministic-ish: temperature 0, fixed rubric.

## Two-tier execution (IMPORTANT — design for it)
Because the judge is local Ollama:
- **Tier 1 (deterministic): planner + refine evals** — pure Python assertions, no
  LLM judge. MUST run anywhere incl. CI. Fast, free, no Ollama dependency.
- **Tier 2 (LLM-judged): recommendation/explanation quality** — needs local Ollama.
  Runs on the dev machine. In CI or when Ollama is absent: SKIP GRACEFULLY (clearly
  reported as "skipped: judge unavailable"), never fail the suite for a missing judge.
- Note: generating the agent OUTPUTS to evaluate calls the real planner/optimizer
  (Groq) and costs TPD budget. Provide a way to eval against CACHED/recorded agent
  outputs so re-running the JUDGE doesn't re-spend Groq tokens. (Record once, judge
  many.)

## Current state
- Live agent: planner (Groq Llama via demo-llama), optimizer (archetypes +
  comparisons), conversational refine (/refine). DemoProvider any-route inventory.
- No eval harness exists. Groq free tier = 100k TPD (shared, easily exhausted) — so
  the harness MUST be token-frugal (cache outputs, judge offline).

### Load-bearing — do NOT change
Tenancy/RLS/resolver, llm_routing.yaml, optimizer prompt/schema, booking SSE
contract. (Wave 2 OBSERVES the agent; it does not modify agent behavior. Wave 3
changes behavior, measured against this.)

## Scope

### 1. Golden dataset
- A curated set of travel queries (start ~20-30) spanning: domestic/intl, one-way/
  round-trip, explicit vs vague dates, cabin mentions, edge cases (typos, ambiguous
  cities). Each entry has the EXPECTED planner extraction (ground truth) and, where
  applicable, the refinement(s) to apply + the expected constraint.
- Stored as versioned data (JSON/YAML) in-repo so the set is reproducible + reviewable.

### 2. Tier-1 deterministic scorers
- **Planner scorer:** run planner on each query, assert extracted fields ==
  expected (origin, dest, dates, cabin, trip_type, pax). Report per-field accuracy
  + overall.
- **Refine scorer:** for refine cases, apply the refinement, assert the constraint
  programmatically (prices dropped / all-morning / 0-stops / etc.). Report pass rate.
- Pure Python; no LLM. Runs in CI.

### 3. Tier-2 LLM-judge (local Ollama)
- For each query's optimizer output (archetypes + comparisons + explanations), the
  Ollama judge scores against a rubric: best-value defensibility, explanation
  accuracy (does it match the actual offer?), relevance, non-genericness.
- Returns per-criterion scores + rationale as JSON. Aggregate to a quality score.
- Graceful skip when Ollama unavailable.

### 4. Eval runner + report
- One command (e.g. `python -m evals.run` or a make target) runs the suite, prints
  a clear report: planner field-accuracy, refine pass-rate, judged quality scores,
  with per-case detail for failures. Machine-readable output (JSON) + human summary.
- Token-frugal: record agent outputs to disk; a `--judge-only` mode re-runs the
  judge on recorded outputs without re-calling Groq.
- Establish a BASELINE run (the current agent's scores) — that's the number Wave 3
  improvements get measured against.

### Out of scope (Wave 3)
- Changing agent behavior to improve scores. New capabilities (multi-city, budget
  opt, preference learning). The model-fallback chain (logged separately).

## Verification
```yaml
- name: tier1_runs_in_ci
  cmd: "python -m evals.run --tier1 (deterministic planner+refine, no Ollama) passes in CI"
  required: true
- name: tier2_local
  cmd: "with Ollama up: python -m evals.run --tier2 produces judged quality scores + rationale"
  required: true
- name: graceful_skip
  cmd: "without Ollama: tier2 skips cleanly (reported, not failed)"
  required: true
- name: token_frugal
  cmd: "--judge-only re-runs judge on recorded outputs with ZERO Groq calls"
  required: true
- name: baseline
  cmd: "a baseline report exists with current agent scores"
  required: true
```

## Escalation rules
- This is additive (a new evals/ module + golden data) — autonomous to build.
- Show me the GOLDEN DATASET design (the query set + what 'expected' means per field,
  and the judge RUBRIC) before mass-authoring it — the eval is only as good as these.
- Escalate before ANY change to agent behavior (out of scope for Wave 2).
- No ANTHROPIC_API_KEY. Judge is local Ollama only. Token-frugal (cache outputs).

## Hard rules
- Wave 2 OBSERVES; it does not modify agent behavior or load-bearing files.
- Deterministic scorers where ground truth exists; LLM-judge ONLY for subjective
  quality. Judge runs on local Ollama (off Groq budget, different family).
- Token-frugal: record-once-judge-many; --judge-only avoids re-spending Groq.
- Tier-1 runs in CI; Tier-2 skips gracefully without Ollama.

## Success criteria
- Golden set (~20-30 cases) versioned in-repo, reviewed.
- Tier-1 deterministic planner + refine scorers run in CI, report accuracy/pass-rate.
- Tier-2 Ollama judge scores recommendation/explanation quality with rubric +
  rationale; skips cleanly when Ollama absent.
- One-command runner + readable report + machine-readable JSON.
- A committed BASELINE of the current agent's scores (the Wave 3 yardstick).
- Token-frugal (--judge-only re-spends zero Groq).

## Build order
1. Golden dataset design (query set + expected-fields + judge rubric) — show me
   BEFORE mass-authoring.
2. Tier-1 deterministic scorers (planner + refine) + CI wiring.
3. Tier-2 Ollama judge (rubric, JSON output, graceful skip).
4. Eval runner + report + record/--judge-only token-frugal mode.
5. Baseline run; commit the baseline report.
```
