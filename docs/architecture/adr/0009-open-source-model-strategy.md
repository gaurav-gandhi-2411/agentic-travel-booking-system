# ADR-0009: Open-Source Model Strategy and Per-Agent Acceptance Bar

**Status:** Accepted — 2026-05-09

---

## Context

A core project goal is demonstrating that fine-tuned open-source models can match or
compete with frontier models on travel-domain agentic tasks. This claim must be:

1. **Specific** — "good enough" is not a shipping criterion. Enterprise buyers evaluating
   the system need concrete numbers to assess risk.
2. **Honest** — some tasks (complex reasoning, nuanced multi-turn dialogue) may not be
   achievable at 7B–14B parameter scale. Acknowledging this is more credible than
   overclaiming.
3. **Achievable given hardware.** The training budget is an RTX 3070 (8 GB VRAM) plus
   free GPU tiers (Google Colab, Kaggle). Money budget is $0.

The six agents span a wide capability range:

- **Narrow agents** (Planner, FlightHunter, HotelHunter, Booking): primarily structured
  extraction, schema validation, and deterministic state transitions. These tasks are
  well-specified with clear correctness criteria.
- **Hard agents** (Optimizer, Conversation): require multi-step reasoning, preference
  modeling, and coherent multi-turn dialogue generation. These are subjectively evaluated.

This asymmetry means a single model and a single threshold cannot cover all agents.

---

## Decision

### Base model selection

**Primary target:** Qwen 2.5 7B Instruct (Apache 2.0 license, ~4.5 GB at Q4\_K\_M
quantization). Reasons:
- Fits in 8 GB VRAM at 4-bit quantization with room for a LoRA adapter during inference.
- Strong benchmark performance in its weight class, particularly on instruction-following
  and structured output tasks relevant to the narrow agents.
- Apache 2.0 license permits commercial deployment without royalty or usage restrictions.
- Active community and unsloth support for QLoRA fine-tuning.

**Secondary target for hard agents:** Qwen 2.5 14B Instruct. LoRA adapter only (no full
fine-tune). Sequence length capped to 2,048 tokens for VRAM budget. Full fine-tune of
14B requires Colab/Kaggle free tier (T4 or A100 with high-RAM runtime).

**Not considered for fine-tuning:** Llama 3.3 70B, Qwen 2.5 72B. These are used as
evaluation comparison points (baseline and judge) but are too large to fine-tune on
available hardware.

### Fine-tuning methodology

**Method:** QLoRA via `unsloth`. Configuration:
- Quantization: 4-bit (NF4 dtype, double quantization enabled)
- LoRA rank: 16 for narrow agents, 32 for hard agents
- LoRA alpha: 16
- Target modules: `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`,
  `down_proj`
- Sequence length: 2,048 (covers all expected agent input/output pairs)
- Training epochs: 3 (narrow agents), 5 (hard agents — more data-hungry tasks)
- Batch size: 2 with gradient accumulation = 4 (effective batch 8, 3070 VRAM budget)

**Training schedule:**
- Phase 6.6: Qwen 2.5 7B for narrow agents (Planner, FlightHunter, HotelHunter, Booking)
  on local RTX 3070. ~6–10 hours per agent per run.
- Phase 8.5: Qwen 2.5 14B for hard agents (Optimizer, ConversationManager) on
  Colab/Kaggle free tier. ~4–8 hours per agent on A100 (Colab Pro Free allocation).

### Per-agent acceptance thresholds

Thresholds are expressed as a percentage of the **frontier baseline**, where 100% means
matching the frontier exactly. The frontier baseline for each agent is defined in
ADR-0010.

| Agent | Task | Metric | Threshold |
|---|---|---|---|
| PlannerAgent | Intent parsing | Schema-correctness on golden set | ≥98% of frontier |
| FlightHunterAgent | Filter + extraction | Accuracy on golden set | ≥95% of frontier |
| HotelHunterAgent | Filter + extraction | Accuracy on golden set | ≥95% of frontier |
| OptimizerAgent | Package ranking + NL explanation | Pairwise judge preference | ≥40% wins-or-ties |
| BookingAgent | State-machine correctness | Binary correctness | 100% (tied) |
| ConversationManagerAgent | Multi-turn coherence | Pairwise judge preference (5 turns) | ≥35% wins-or-ties |

**Threshold rationale:**

- Planner and hunter agents (98%, 95%): high thresholds because these are structured
  tasks with deterministic correct answers. A 7B model that misparses 5% of flight search
  parameters is a functional regression, not an acceptable tradeoff.
- BookingAgent (100%): state-machine correctness is binary. A booking agent that
  incorrectly sequences lock → confirm is not safe to deploy regardless of how close it
  is to frontier. This is not a research question; it is a correctness requirement.
- OptimizerAgent (40% wins-or-ties): pairwise judge preference is inherently noisy. 40%
  wins-or-ties means the fine-tuned model is competitive — not clearly worse — even if
  it does not dominate. The frontier model will likely produce superior NL explanations;
  40% is the pragmatic bar that justifies deployment over a non-fine-tuned baseline.
- ConversationManager (35% wins-or-ties): multi-turn coherence at 5 turns is a hard task
  for 7B/14B models. 35% is deliberately set below 40% to reflect the acknowledged
  difficulty. A model at 35% wins-or-ties is meaningfully better than random (50% would
  be parity) while accepting that frontier quality is not achievable in this weight class.

**Below-threshold behavior:** An agent that does not meet its threshold after fine-tuning
does not ship as the fine-tuned variant. The agent falls back to the OpenRouter free-tier
70B model in production. This is recorded in the technical report (ADR-0012) as an honest
research outcome, not a failure.

**Threshold revisability:** These thresholds are the initial targets set before any
baseline run. After Phase 3.5 (Baseline Benchmarks), the numbers will be revisited if
the baseline reveals that the frontier bar is higher or lower than expected. Any revision
requires updating this ADR with a dated amendment.

### Frontier baselines

Two baseline tiers, used for different purposes:

**Tier 1 — Programmatic baseline (automated, in CI):**
- Llama 3.3 70B Instruct (via OpenRouter free tier)
- Qwen 2.5 72B Instruct (via OpenRouter free tier)
- Run against the full golden dataset for each agent
- Used to compute the percentage thresholds above

**Tier 2 — Frontier baseline (manual, not in CI):**
- Claude Sonnet 4.6 (via Claude.ai — no API key required)
- Used for qualitative spot-checks and the technical report
- Not used for automated threshold computation (cost)

---

## Consequences

**Positive:**
- Clear, agent-specific thresholds give the research track a defensible "done" condition.
  The system ships each agent's fine-tuned variant only when the bar is met.
- The 7B/14B split acknowledges hardware reality without pretending all agents have the
  same difficulty profile.
- Apache 2.0 base model means published checkpoints (ADR-0012) have no licensing
  constraints for downstream commercial use of the base weights.
- Honest about the ConversationManager threshold — buyers know in advance that this agent
  may ship on 70B rather than 7B/14B, and why.

**Negative:**
- The 35% ConversationManager threshold may not be achievable even with fine-tuning.
  Accepted: if the fine-tuned model does not reach 35%, it ships on the 70B fallback,
  and the technical report documents the gap. This is not a project failure — it is a
  data point.
- LoRA adapters for the 14B model require Colab/Kaggle free tier, introducing dependency
  on external compute availability and session time limits. Training may need to be split
  across multiple sessions with checkpoint resumption.
- Acceptance thresholds are expressed relative to a moving target (frontier models
  improve over time). The thresholds are locked to the baseline run in Phase 3.5; later
  frontier improvements do not retroactively raise the bar.

**Neutral:**
- The Qwen 2.5 model family is controlled by Alibaba. Model deprecation or license
  changes would require re-evaluation. Apache 2.0 weights are redistributable, so
  existing checkpoints are not at risk, but future fine-tuning would need an alternative.

---

## Alternatives Considered

### Alternative 1: Frontier models only (no fine-tuning)

Use Claude, GPT-4o, or Gemini for all agents in production.

**Rejected because:** cost eliminates the $0 budget constraint. More importantly, this
removes the open-source research track, which is a core USP for the technical report and
the Hugging Face artifacts that support the B2B sales story.

### Alternative 2: Untargeted fine-tuning (no per-agent thresholds)

Fine-tune on all agents together, declare success when qualitative evaluation seems good.

**Rejected because:** "seems good" is not a shipping criterion. Without thresholds, there
is no principled decision about when to ship a fine-tuned agent vs. the fallback model.
Enterprise buyers evaluating the system for procurement need numbers.

### Alternative 3: Match-frontier-or-bust

Set all thresholds at 95%+ wins-or-ties, including Optimizer and Conversation.

**Rejected because:** unrealistic at 7B–14B scale for the hard agents. A 7B model at
95%+ parity with Sonnet on multi-turn conversational coherence has not been demonstrated
in the literature for this class of task. Setting an unachievable bar and then shipping
anyway is less credible than setting an achievable bar and meeting it.

### Alternative 4: Mistral 7B or Llama 3.2 3B as primary target

Alternative open-source models in the sub-10B range.

**Rejected because:** Qwen 2.5 7B outperforms Mistral 7B v0.3 on the specific task types
(structured instruction-following, JSON output, code) relevant to the narrow agents in
published benchmarks as of the decision date. Llama 3.2 3B is too small for the Planner
and Optimizer agents even with fine-tuning. Qwen 2.5's Apache 2.0 license and unsloth
support are decisive practical factors.

---

*Referenced plan.md sections: §12, §11 (Phases 6.6, 8.5), §17, §20*
*See also: ADR-0008 (LLM routing), ADR-0010 (eval harness), ADR-0011 (dataset generation), ADR-0012 (publishing)*
