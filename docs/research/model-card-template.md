# Model Card — DealHunter [AGENT] Adapter

<!-- Replace [AGENT] with the agent name (e.g., "Planner") -->

## Model Details

| Field | Value |
|-------|-------|
| **Base model** | Qwen/Qwen2.5-7B (or 14B for optimizer/conversation) |
| **Adapter type** | PEFT LoRA (QLoRA fine-tuning via unsloth) |
| **LoRA rank** | 16 (narrow agents) / 32 (optimizer, conversation) |
| **License** | CC-BY-NC-4.0 (adapter weights only) |
| **Base model license** | Apache 2.0 (separate; not redistributed) |
| **Training data** | DealHunter synthetic + human-QA'd golden dataset |
| **Published** | [YYYY-MM-DD] |

## Scope of License

The CC-BY-NC-4.0 license applies **only** to the LoRA adapter weights and
delta parameters in this repository. The Qwen 2.5 base model remains under
its original Apache 2.0 license. You must obtain the base model separately
from the Qwen team. Do not redistribute the base model or a merged checkpoint
under this card's license.

## Intended Use

This adapter fine-tunes the base model for the **[AGENT]** role in a
multi-agent travel booking system. It is not suitable for general-purpose
chat, instruction following outside the travel domain, or safety-critical
applications.

## Out-of-Scope Use

- General-purpose assistant (use base Qwen 2.5 instead)
- Commercial deployment without a separate commercial license
- Any use that violates Qwen's Apache 2.0 terms for the base model

## Performance

| Metric | Fine-tuned | Base model | Frontier baseline |
|--------|-----------|------------|-------------------|
| exact_match | — | — | — |
| preference_win_rate | — | — | — |

*(Populated after Phase 6.5 fine-tuning run)*

## Training Details

- **Dataset**: [N] examples ([N_train] train / [N_eval] eval)
- **Hardware**: [GPU]
- **Framework**: unsloth + HF PEFT + Transformers ≥4.40.0
- **Training steps**: [N]
- **Hyperparameters**: rank=[R], alpha=[A], lr=[LR], batch=[B]

## Eval Protocol

See `evals/` and `docs/research/benchmark-protocol.md`.

## Citation

```bibtex
@misc{dealhunter2026,
  title  = {DealHunter: Fine-Tuning Compact Open-Source Models for Agentic Travel Booking},
  author = {[Authors]},
  year   = {2026},
  url    = {[URL]}
}
```
