# ADR-0010: Eval Harness Design

**Status:** Accepted — 2026-05-09

---

## Context

The project makes a testable empirical claim: fine-tuned open-source models can match or
compete with frontier models on travel-domain agentic tasks. This claim requires
defensible measurement. "Defensible" means:

1. **Reproducible.** A skeptical buyer or reviewer should be able to run the same eval
   and get the same headline numbers (within noise) using the published checkpoints and
   eval runner.
2. **Automated where possible.** Manual evaluation does not scale to CI and cannot catch
   regressions between training runs.
3. **Honest about what is automated vs. manual.** LLM-as-judge has known biases.
   Pairwise preference is more robust than absolute scoring. Manual spot-checks via
   Claude.ai serve a different purpose than CI gates.
4. **A real CI gate.** Eval must block merges on regression, not run with
   `continue-on-error: true`.

The agents span two evaluation paradigms:

- **Deterministic correctness** (Planner, FlightHunter, HotelHunter, Booking): a correct
  output is defined by a schema and a set of expected field values. Automated scoring
  against a golden dataset is sufficient and reliable.
- **Subjective quality** (Optimizer, Conversation): correctness is not well-defined.
  "Is this a good travel package explanation?" requires a judge. The judge is itself a
  model, introducing bias. Pairwise preference (is A better than B?) is less noisy than
  absolute scoring (rate A from 1–5).

---

## Decision

The eval harness has three components and two CI modes.

### Component 1: Golden datasets

Location: `evals/datasets/<agent>/golden.jsonl`

Format: one JSON object per line. Each object has:
```json
{
  "id": "planner-001",
  "input": { ... },
  "expected_output": { ... },
  "tags": ["round_trip", "domestic", "budget_traveler"],
  "difficulty": "medium",
  "notes": "Optional human annotation"
}
```

- 50–100 examples per agent for the initial eval suite (Phase 3.5).
- Target 100 examples per agent at Phase 6.7 (post-fine-tuning iteration).
- Examples are **never used for training.** A separate training set lives in
  `evals/datasets/<agent>/train/`. The golden set is held out as a clean test partition.
- 20% of golden examples will be published on Hugging Face Datasets (ADR-0012). The
  80% retained set provides a harder evaluation that published models cannot overfit to.

Dataset provenance is documented in `evals/datasets/README.md` for each agent: how
examples were generated (ADR-0011), who reviewed them, and the date of last update.

### Component 2: Judge prompts

Location: `evals/judges/<agent>.txt`

Used only for Optimizer and ConversationManagerAgent, where correctness is subjective.

Format: a pairwise preference prompt. Given two outputs A and B (randomly ordered to
control for position bias), the judge model returns `{"winner": "A" | "B" | "tie",
"reason": "..."}`. The runner calls the judge model twice with A/B swapped and reports
a win only if the same answer wins both orderings (otherwise: tie). This reduces
position-bias errors by ~40% based on published findings on pairwise eval robustness.

Judge model selection:
- **Primary:** Qwen 2.5 72B Instruct via OpenRouter free tier. Selected for its strong
  instruction-following and OpenRouter free availability.
- **Cross-check:** Llama 3.3 70B Instruct via OpenRouter free tier. Run on a sample of
  10–15 examples per eval run to confirm the primary judge's direction.
- **Manual spot-check:** Claude Sonnet 4.6 via Claude.ai. Not in CI. Documented in
  `evals/manual/` with input, both outputs, and human notes.

No frontier model is used as a judge in CI — cost.

### Component 3: Test runner

Entry point: `evals/run.py`

```
python evals/run.py --agent planner --model qwen2.5:7b --profile local [--subset 20]
```

Output: JSON file in `evals/results/<agent>/<model>/<timestamp>.json`

```json
{
  "agent": "planner",
  "model": "qwen2.5:7b",
  "profile": "local",
  "n_examples": 100,
  "schema_validity": 0.98,
  "task_accuracy": 0.95,
  "judge_score": null,
  "mean_latency_ms": 1240,
  "mean_input_tokens": 512,
  "mean_output_tokens": 184,
  "timestamp": "2026-05-09T14:32:00Z",
  "baseline_model": "qwen/qwen-2.5-72b-instruct:free",
  "baseline_task_accuracy": 0.97
}
```

`judge_score` is `null` for agents evaluated on task accuracy; a float in [0, 1] for
pairwise-judged agents (proportion of wins-or-ties vs. baseline).

The runner also computes the **delta vs. baseline** and prints a regression report:
```
PASS  planner   task_accuracy  0.95 vs baseline 0.97  (delta: -2.1%)  [within 2% gate]
FAIL  optimizer judge_score    0.31 vs baseline 0.40  (delta: -22.5%) [REGRESSION]
```

### CI modes

**`make eval-quick`** — subset of 20 examples per agent, runs on every PR that touches
model-relevant files. Target runtime: ~2 minutes on a GitHub Actions runner.

Trigger condition: changes to any of:
- `apps/api/src/travel_agent/agents/**`
- `apps/api/src/travel_agent/llm/**`
- `evals/datasets/**`
- `evals/judges/**`
- `evals/run.py`

**`make eval-full`** — complete golden dataset, runs nightly at 02:00 UTC on `main`.
Target runtime: ~30 minutes.

**Regression gate:** If `eval-quick` shows a drop of **more than 2 percentage points**
on any metric for any agent, the CI job fails. `continue-on-error: false`. No escape
hatch. A failing eval must be investigated and addressed before merge — either by fixing
the model/prompt or by updating the baseline in a deliberate, reviewed commit.

The 2% gate is intentionally loose for `eval-quick` (20 examples has high variance) and
stricter in practice for `eval-full` (100 examples, tighter confidence interval). The
gate threshold for `eval-full` nightly is also 2% but triggers a GitHub issue rather
than a blocked merge (nightly runs are on main, not PRs).

---

## Consequences

**Positive:**
- The eval harness is the ground truth for the research track. Results in the technical
  report (ADR-0012) are generated by this runner and reproducible by anyone with the
  published checkpoints.
- Automated regression detection means a bad fine-tuning run cannot silently degrade a
  previously-passing agent.
- Pairwise preference with double-swap is a more robust judge design than single-pass
  scoring.
- The JSON results format makes it easy to build a simple comparison dashboard (HTML
  or notebook) for Phase 11.5.

**Negative:**
- Judge-model bias is real and partially unavoidable. Qwen 2.5 72B as judge may favor
  Qwen-style outputs. The Llama 3.3 70B cross-check partially mitigates but does not
  eliminate this. The technical report must disclose the judge model and this limitation.
- OpenRouter rate limits (50 req/day on free tier) constrain how often `eval-full` can
  run judge evaluations. Full nightly runs may need to batch judge calls across multiple
  days for large agent counts. Practical mitigation: run judge scoring only on the 10%
  of examples where task accuracy differs between models (the interesting cases).
- 20-example `eval-quick` subsets have high variance. A 2% gate may trigger false
  positives (a good change fails eval-quick noise). Accepted: false positives prompt
  investigation, which is better than false negatives (regressions pass silently).

**Neutral:**
- The eval suite (~500 LOC) is itself a deliverable. It ships in the open repo under MIT
  license (ADR-0012), so buyers can verify the methodology independently.
- Example counts (50–100 per agent) are the minimum viable for Phase 3.5. The stretch
  target of 50+ from plan.md §12 remains the goal for Phase 11.

---

## Alternatives Considered

### Alternative 1: Human-only evaluation

All results are human-judged. No automated runner.

**Rejected because:** not reproducible, not scalable to CI, and not sufficient for the
baseline comparison claims. Human evaluation is preserved as a complement (manual
spot-checks in `evals/manual/`) but cannot be the primary mechanism.

### Alternative 2: Frontier model as primary judge in CI

Use Claude Sonnet 4.6 or GPT-4o as the CI judge for all agents.

**Rejected because:** cost. At $0 budget with $0 CI spend, every judge call in CI is a
violation of the budget constraint. Claude.ai qualitative spot-checks are the right
boundary for frontier judge involvement.

### Alternative 3: Single test suite per agent (no golden dataset, no runner)

Use pytest-style unit tests with hardcoded expected outputs.

**Rejected because:** too easy to overfit the fine-tuned model to the exact test cases,
and not reproducible as a research artifact. A golden dataset with provenance documentation
is a publishable, citable artifact; hardcoded pytest expectations are not.

### Alternative 4: BLEU / ROUGE metrics for subjective agents

Compute BLEU or ROUGE scores for Optimizer and Conversation outputs rather than using
a judge model.

**Rejected because:** n-gram overlap metrics are not meaningful for travel explanation
quality. A response that uses different words to express the same preference ranking is
not worse than one that matches the reference string exactly. Pairwise preference captures
what we actually care about — which output a user would find more useful.

---

*Referenced plan.md sections: §12, §11 (Phases 2.5, 3.5, 6.7), §17, §20*
*See also: ADR-0009 (acceptance thresholds), ADR-0011 (dataset generation), ADR-0012 (publishing)*
