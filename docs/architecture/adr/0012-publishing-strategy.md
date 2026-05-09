# ADR-0012: Publishing Strategy for Models, Datasets, and Methodology

**Status:** Accepted — 2026-05-09

---

## Context

The project has a dual purpose: a production-grade multi-agent travel booking system (the
product track) and a research contribution demonstrating that fine-tuned open-source
models can match frontier models on travel-domain agentic tasks (the research track).

The B2B sales story requires that prospects can independently evaluate the research claims
before purchasing a license or entering a deployment agreement. Fully closed artifacts
(models not published, dataset not published, methodology not public) eliminate this
independent evaluation path and reduce credibility.

At the same time, the commercial moat depends on maintaining some proprietary advantage.
Full openness — releasing training data, fine-tuned models under permissive licenses, and
eval datasets — removes the incentive to pay for access.

The tension is between **credibility** (requiring openness) and **commercial value**
(requiring some closure). This ADR resolves that tension artifact by artifact.

A secondary constraint: the base model for fine-tuning is Qwen 2.5 (Apache 2.0). Any
license applied to fine-tuned artifacts must be compatible with Apache 2.0's terms.
CC-BY-NC-4.0 is more restrictive than Apache 2.0 but is legally applicable to derivative
works created by fine-tuning.

---

## Decision

### Asymmetric openness by artifact type

| Artifact | License | Where Published | Status |
|---|---|---|---|
| Fine-tuned LoRA adapter weights | CC-BY-NC-4.0 | Hugging Face Hub | Public |
| Eval methodology + runner code | MIT | This repo | Public |
| Judge prompts (`evals/judges/`) | MIT | This repo | Public |
| 20% golden dataset sample | CC-BY-4.0 | Hugging Face Datasets | Public |
| 80% golden dataset | Commercial IP | Not published | Private |
| Dataset generation scripts | MIT | This repo (`scripts/dataset/`) | Public |
| Training dataset (raw + QA-filtered) | Not published | Local / HF private | Private |
| Technical report | CC-BY-4.0 | `docs/research/` + blog | Public |
| Benchmark reproduction protocol | MIT | `docs/research/benchmark-protocol.md` | Public |

### Scope of license for fine-tuned adapter weights

**The CC-BY-NC-4.0 license applies only to the LoRA adapter weights, fine-tuning
artifacts, and merged-checkpoint deltas produced by this project. The Qwen 2.5 base
model remains under its original Apache 2.0 license; downstream users obtain it from
Alibaba's official Hugging Face repository, not from us. We never redistribute the
base model. Users who load our adapters do so on top of the Apache 2.0 base they
download separately.**

**Practical implication:** the model card on Hugging Face will be tagged as a PEFT
adapter with the base model dependency declared, not as a standalone model. The license
field is CC-BY-NC-4.0 with a note clarifying it covers only the adapter weights.

This boundary is important for downstream users: they can use the base Qwen 2.5 model
commercially (Apache 2.0 permits this), but they cannot use our LoRA adapters for
commercial purposes without a license agreement with this project.

### Hugging Face publishing details

**Model repository naming:** `<org>/agentic-travel-<agent>-qwen2.5-7b-lora` per agent,
under a project Hugging Face organization created in Phase 11.5.

**Model card requirements per adapter:**
- Base model declared: `Qwen/Qwen2.5-7B-Instruct`
- License: CC-BY-NC-4.0 (with scope note above)
- Task: specific agent task description
- Training data: "Synthetic, generated via Qwen 2.5 72B teacher + manual QA. See
  dataset card and ADR-0011 for methodology."
- Evaluation results: task accuracy and judge score vs. baseline, from the Phase 6.7 eval
- Limitations: explicitly noting where the model falls short of frontier quality
- Usage: inference code snippet using PEFT + base model

**Dataset repository naming:** `<org>/agentic-travel-eval-sample` (combined sample
across all agents, 20% of golden sets).

**Dataset card requirements:**
- Provenance: teacher model, self-critique pass, QA protocol, date
- Format specification: JSON schema for each agent's examples
- Split: only the eval split is published; training data is not included
- License: CC-BY-4.0

### Technical report

A PDF published in `docs/research/technical-report.pdf` and linked from a blog post.
Content outline (defined in `docs/research/paper-outline.md`):
- Introduction: the fine-tuning-for-agentic-tasks problem
- System description: the six-agent architecture (summarizing public ADRs)
- Methodology: QLoRA fine-tuning, evaluation harness design, dataset generation
- Results: per-agent accuracy and judge scores vs. baseline, with confidence intervals
- Ablations: LoRA rank comparison, sequence length effect, epoch count effect
- Limitations: honest discussion of where open-source models fall short
- Reproducibility: pointer to `docs/research/benchmark-protocol.md`

The technical report is the primary sales artifact for the research track. Its headline
numbers must be reproducible by a buyer's technical team using the published checkpoints
and the published 20% eval sample.

### Benchmark reproducibility

`docs/research/benchmark-protocol.md` documents the exact steps to reproduce the
reported numbers:
1. Install dependencies (PEFT, transformers, the eval runner)
2. Download the published LoRA adapter from Hugging Face
3. Download the 20% eval sample from Hugging Face Datasets
4. Run `python evals/run.py --agent <name> --model <published-adapter>`
5. Compare output JSON to the numbers in the technical report

This protocol runs in ~2 hours on a machine with a consumer GPU (RTX 3070 equivalent or
better). It requires no API keys (all inference is local). It produces numbers within
±2% of the reported values (natural variation from generation temperature and hardware).

---

## Consequences

**Positive:**
- The asymmetric openness satisfies both constraints: buyers can independently reproduce
  headline numbers (credibility) while the training data and 80% eval set remain
  proprietary (commercial value).
- Published eval code under MIT means the methodology can be cited and extended by
  the research community, increasing the project's credibility and reach.
- CC-BY-NC-4.0 adapters allow academic and non-commercial evaluation without friction,
  while requiring commercial users to engage with the licensing process — creating a
  natural sales pipeline.
- Hugging Face model cards with explicit limitation disclosures (where open-source
  models fall short) signal research integrity, which is valued by the technical buyers
  who review the repo before procurement.

**Negative:**
- A 20% eval sample may not be large enough for rigorous external reproduction. A
  determined skeptic who samples 20 examples may hit high-variance estimates. The
  benchmark-protocol.md should acknowledge this and provide confidence intervals.
- CC-BY-NC-4.0 license enforcement at this scale is honor-system. We do not have the
  resources to police commercial use of published adapters. This is accepted — the goal
  is to create a licensing friction point for commercial buyers, not to eliminate all
  unauthorized commercial use.
- Publishing negative results (agents where the fine-tuned model does not meet the
  acceptance bar) may reduce the perceived strength of the research contribution. This
  is mitigated by framing: agents that fall below threshold ship on the 70B fallback,
  and the technical report positions this as an honest assessment of model capability
  at each scale, not a failure.

**Neutral:**
- Hugging Face organization creation (Phase 11.5) requires an account. No cost.
- The CC-BY-NC-4.0 / Apache 2.0 license stack creates a dual-layer artifact where the
  base model is freely usable commercially and the adapter is not. This is a known
  pattern in the HF community (e.g., Alpaca's similar non-commercial restriction on
  fine-tuned weights over LLaMA 1's license). Downstream users understand this pattern.
- The dataset card's "Provenance: Qwen 2.5 72B teacher" disclosure is important for
  users who need to understand the data quality ceiling. A training set distilled from
  a 72B teacher cannot produce a 7B student that exceeds 72B quality — this is expected
  and stated.

---

## Alternatives Considered

### Alternative 1: Fully open (everything MIT or Apache 2.0)

Release all artifacts under permissive licenses: training data, fine-tuned models,
eval dataset, methodology.

**Rejected because:** no commercial moat. If the training data and fine-tuned models are
fully open, any competitor can reproduce the system without licensing it. The B2B sales
story depends on the proprietary training data and the retained 80% eval set as
differentiators.

### Alternative 2: Fully closed (nothing published)

No public artifacts. Buyers evaluate the system only through a demo or a private
technical preview.

**Rejected because:** no credibility. Enterprise technical buyers evaluating the
"open-source matches frontier" claim need to run numbers themselves. A non-reproducible
claim in a sales deck is not the same as a verifiable benchmark. Fully closed artifacts
remove the project from the research conversation entirely.

### Alternative 3: Time-delayed open (publish all under permissive license after 12 months)

Release everything under CC-BY-SA or Apache 2.0 after a 12-month commercial exclusivity
window.

**Rejected because:** complexity. Time-gated licensing requires infrastructure to enforce
(or at minimum track) the delay and manage the transition. It also does not serve the
credibility requirement — buyers cannot evaluate before the 12-month window expires.
The asymmetric-by-type approach achieves the same goal (some openness now, some
proprietary advantage retained) without the time-gating complexity.

### Alternative 4: CC-BY-NC-4.0 for all artifacts

Apply non-commercial restriction uniformly across eval code, methodology, and adapters.

**Rejected because:** it restricts the research community from building on the methodology.
MIT for code and eval tools maximizes academic adoption and citation potential. The
commercial restriction is appropriately targeted only at the fine-tuned weights, which
are the commercially valuable artifact.

---

*Referenced plan.md sections: §17, §11 (Phase 11.5), §20*
*See also: ADR-0009 (acceptance thresholds), ADR-0010 (eval harness), ADR-0011 (dataset generation)*
*See also: docs/research/README.md, docs/research/paper-outline.md, docs/research/benchmark-protocol.md*
