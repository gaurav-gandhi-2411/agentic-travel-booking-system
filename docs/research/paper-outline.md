# Technical Report Outline

**Working title**: *DealHunter: Fine-Tuning Compact Open-Source Models for Agentic Travel Booking*

## Abstract (target: 250 words)

Summary of the system, fine-tuning approach, key results vs. frontier baseline,
and the public eval dataset contribution.

## 1. Introduction

- Problem: agentic travel booking requires multiple specialised sub-tasks
- Hypothesis: per-agent QLoRA fine-tuning of 7B/14B models can match frontier performance on narrow tasks
- Contributions: adapters, eval dataset sample, harness, benchmark results

## 2. System Architecture

- Multi-agent architecture overview (reference plan.md §4)
- Agent roles and task decomposition
- LLM routing by profile (ADR-0008)

## 3. Dataset Generation

- Diversity matrix and seed design (ADR-0011)
- Teacher model: Qwen 2.5 72B via OpenRouter free tier
- Self-critique chain
- Human QA process and inter-annotator agreement
- Final dataset statistics per agent

## 4. Fine-Tuning

- Base models: Qwen 2.5 7B (narrow agents), 14B (optimizer/conversation)
- QLoRA configuration: rank 16 (narrow), rank 32 (hard agents), alpha=16
- Training framework: unsloth + HF PEFT + Transformers
- Hardware and runtime estimates
- Hyperparameter choices and ablations

## 5. Evaluation

- Eval harness design (ADR-0010)
- Per-agent metrics and pass thresholds (ADR-0009)
- Pairwise preference protocol (double-swap, position bias mitigation)
- Judge models: Qwen 2.5 72B primary, Llama 3.3 70B cross-check
- Results table: fine-tuned vs. frontier baseline vs. base model

## 6. Results

- Per-agent pass/fail vs. threshold
- Win/tie/loss rates vs. claude-sonnet-4-6 baseline
- Latency and cost comparison

## 7. Limitations

- Dataset size constraints (free-tier rate limits, ~50 req/day)
- Judge model bias risks
- 80% of the golden eval dataset is proprietary and not publicly released; external reproducers work from the 20% public sample only — results may differ from those reported here due to sample variation
- Scope: travel booking domain only; transferability not evaluated

## 8. Future Work

- vLLM serving for production inference (ADR-0008)
- Expanding to multi-turn conversation fine-tuning
- Larger eval dataset with crowd-sourced QA

## Appendix A: Hyperparameter Tables

## Appendix B: Example Generations (pre/post fine-tune)

## Appendix C: Eval Dataset Card
