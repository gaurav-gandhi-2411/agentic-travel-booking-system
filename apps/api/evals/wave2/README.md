# Wave 2 Eval Harness

Evaluates the Wave 2 planner + refine + optimizer pipeline on 31 golden cases.

## Structure

```
evals/wave2/
  golden.json          # 31 test cases (28 planner, 3 refine)
  runner.py            # generates planner + refine + optimizer outputs → runs/
  scorer.py            # Tier-1 deterministic field accuracy
  judge.py             # Tier-2 LLM quality scoring via Ollama
  runs/                # JSONL output from runner (one file per run)
  reports/             # markdown reports from scorer and judge
  README.md            # this file
```

## Two-tier evaluation

### Tier-1 (deterministic) — `scorer.py`

Scores the planner's structured output against golden field expectations:

| Metric | What it checks |
|---|---|
| Required fields | origin_iata, destination_iata, trip_type, cabin_class — must match exactly |
| Optional fields | budget_inr, traveler_count, hotel_min_stars, airline_preference, departure_time_constraint, trip_duration_days |
| Departure window | earliest/latest departure bounds with ±1-day tolerance |
| Refine pass-rate | direct_only / price_sort / morning_departure constraints applied correctly |

Run: `python -m evals.wave2.scorer`

### Tier-2 (LLM judge) — `judge.py`

Scores the optimizer's archetype explanations on 4 criteria using a local qwen3:8b judge
(Ollama). Requires Ollama running with `qwen3:8b` pulled.

Run: `python -m evals.wave2.judge`

#### 4-criterion rubric

| Criterion | What it measures | Range |
|---|---|---|
| factual_accuracy | Explanation accurately describes the actual flight (price, stops, airline, time) | 1–5 |
| value_defensibility | Explanation makes a grounded case for this archetype; not a bare assertion | 1–5 |
| specificity | Explanation cites actual flight details, not generic filler | 1–5 |
| traveler_framing | Explanation speaks to traveler outcomes, not raw features | 1–5 |

`overall_pass` is computed by the scorer from these per-criterion scores, not by the judge.

#### Known judge limitation: specificity is a soft criterion on qwen3:8b

**Validated 2026-06-21** via a 4-case discrimination probe:

| Test case | factual_accuracy | value_defensibility | specificity | traveler_framing |
|---|---|---|---|---|
| GENERIC filler ("Great flight, highly recommended") | 5 | 3 | **4** | 3 |
| FACTUALLY WRONG (price and stop count both wrong) | **2** | 3 | 3 | 3 |
| MISLABELED (economy 2-stop called "most luxurious") | 5 | **1** | 5 | **1** |
| GENUINELY GOOD (accurate, specific, traveler-framed) | 5 | 5 | 5 | 5 |

`factual_accuracy`, `value_defensibility`, and `traveler_framing` discriminate correctly —
bad cases drop the relevant criterion to 1–2. `specificity` does NOT reliably penalize
generic text (filler scored 4/5 instead of the correct 1/5).

**When reading Tier-2 baselines:**
- Use `factual_accuracy + value_defensibility + traveler_framing` as the primary signal (3-criterion average)
- Treat `specificity` as low-confidence / potentially inflated
- The `overall_quality` average includes specificity; note this when comparing runs

**Future improvement:** tighter specificity sub-prompt or switching to `qwen3:30b-a3b`
(logged, not blocking the Wave 2 baseline).

## Running the full eval

```bash
# 0. Probe Groq TPD headroom first
python -m evals.wave2.runner --probe

# 1. Generate outputs (requires GROQ_API_KEY, OPENROUTER_API_KEY, and Ollama).
#    See "Groq TPD budget" below -- the full 31-case run does not fit in one
#    window, so this is normally two-or-more invocations, not one.
python -m evals.wave2.runner --profile demo-llama --no-fallback   # authoritative baseline

# 2. Tier-1 accuracy
python -m evals.wave2.scorer

# 3. Tier-2 quality
python -m evals.wave2.judge
```

### Fallback and the authoritative baseline

`demo-llama`'s planner, optimizer, AND conversation (the refine-case classifier) all
route through a Groq -> OpenRouter (Gemma-4-31B) fallback chain by default (spec.md,
ADR-0027) — a Groq TPD wall no longer hard-blocks generation. **The runner defaults to
fallback ON.**

This is a double-edged sword for eval baselines: a run that mixes Groq Llama-3.3-70B
and OpenRouter Gemma-4-31B output is not a clean measurement of one model. Every
record carries `served_model_planner`, `served_model_conversation`,
`served_model_optimizer`, and a `fallback_used` flag; a non-empty set of flagged cases
prints a warning at the end of the run.

**Rule:** the AUTHORITATIVE Wave 2 baseline (the one the Tier-1 CI gate and Tier-2
judge score against) MUST be generated with `--no-fallback`, so every case is served
by the same configured model. Use the fallback (default, no flag) only for:
- Resilient production search (wired into the search/refine API routes, not the eval path)
- Ad-hoc/non-blocking eval reruns where you need SOME output despite Groq TPD
  exhaustion and will not treat the result as the authoritative baseline

```bash
# Authoritative baseline (required)
python -m evals.wave2.runner --profile demo-llama --no-fallback

# Non-blocking resilience rerun (mixed-provider, NOT authoritative -- inspect
# fallback_used before trusting any score delta)
python -m evals.wave2.runner --profile demo-llama
```

### Groq TPD budget — KNOWN CONSTRAINT

The `demo-llama` profile uses `llama-3.3-70b-versatile` for planner, optimizer, AND
conversation agents. The 100k tokens/day limit applies to ALL three.

**Measured per-call cost (from 2026-06-21 partial run):** ~1,123 tokens/call average.

| Metric | Count |
|---|---|
| Planner calls (31 cases) | 31 |
| Optimizer calls (31 × 3: explain×2 + compare×1) | 93 |
| Refine calls (3 refine cases) | 3 |
| **Total** | **127 calls** |
| **Estimated tokens** | **~143,000** |
| Groq daily limit | 100,000 |

**The full 31-case run CANNOT complete in a single 100k TPD window.** Even from a
completely clean window (0 tokens pre-consumed), the run needs ~143k tokens vs 100k
allowed — and **the optimizer step alone (93 calls, ~104k tokens) already exceeds the
100k ceiling by itself**, confirmed empirically 2026-07-05. This means even the
"planner+refine now, optimizer separately" split needs the optimizer half spread
across **at least 2** clean windows, not one.

Groq's TPD bucket is a **rolling 24h window**, not a fixed daily-reset clock — usage
ages out continuously (confirmed empirically: a 429's "try again in Xm" scales with
how much of a given request's token cost needs to age out, not a fixed countdown to
midnight). Practically: after a heavy burst, remaining headroom recovers gradually
over the following ~24h, not all at once.

#### Split-run options

1. **Tier-1 first, optimizer via `--resume-from` across multiple windows (recommended)**
   ```bash
   # Window 1: planner + refine, all 31 cases (~38k tokens, fits easily)
   python -m evals.wave2.runner --profile demo-llama --no-fallback --no-optimizer

   # Window 2 (next clean window): optimizer for as many cases as fit. Reuses the
   # cached planner/refine output from window 1 -- does NOT re-spend those tokens.
   python -m evals.wave2.runner --profile demo-llama --no-fallback \
     --resume-from runs/<window-1-file>.jsonl

   # Window 3+ (repeat against the latest run file until every case's
   # optimizer_archetypes is populated): each pass only spends tokens on
   # cases neither planner NOR optimizer succeeded for yet.
   python -m evals.wave2.runner --profile demo-llama --no-fallback \
     --resume-from runs/<window-2-file>.jsonl
   ```
   `--resume-from` reuses any already-succeeded planner/refine/optimizer call from
   the given prior run file — a case is only re-attempted if that specific call is
   still missing. This is what makes multi-window assembly token-frugal instead of
   restarting from zero each time.

2. **Change optimizer to llama-3.1-8b-instant for eval runs**
   - `llama-3.1-8b-instant` has 500k TPD (separate model bucket)
   - Planner stays on 70B (100k bucket): 31 + 3 = 34 calls × ~1,800 = ~61k tokens
   - Optimizer on 8B (500k bucket): 93 calls, no TPD pressure, all in one window
   - Tradeoff: Tier-2 scores reflect 8B explanations, not 70B

3. **Use --limit 22 to cap the run to what fits (22 cases × ~4.5k = ~99k tokens)**
   - Not recommended — partial baselines are not authoritative

Always probe before starting: `python -m evals.wave2.runner --probe`. The probe
deliberately over-requests (at the model's own max_tokens ceiling, 32768) so a 429's
error body reveals the real Used/Limit/reset-eta — Groq's success-response headers
never carry daily-bucket figures at all, only per-minute ones. A single probe call
can therefore only ever confirm a lower bound (~32.7k), never the full 100k, since
32768 < 100,000.

A partial run (429 mid-way) produces an incomplete baseline — do not score partial runs as
authoritative. Use `--resume-from` to complete it token-frugally on a later window
instead of re-running from scratch.

## Synthetic flight pool

The scorer and optimizer both use a 6-flight synthetic pool with fixed prices, stop counts,
and departure times. The pool is designed to exercise the optimizer's Pareto frontier logic:

- **Optimizer path** (`runner.py`): pool is route-matched per case — `origin_iata`,
  `destination_iata`, and `cabin_class` are substituted from the planned intent. This
  ensures the judge sees the correct route in optimizer explanations.
- **Refine path** (`scorer.py`): pool stays BOM→NRT regardless of case route — refine
  constraints (direct_only, price_sort, morning_departure) are route-agnostic.

Pool properties: 0-stop ₹35k, 1-stop ₹28k, 2-stop ₹22k, 0-stop ₹48k, 1-stop ₹31k, 0-stop ₹52k.
