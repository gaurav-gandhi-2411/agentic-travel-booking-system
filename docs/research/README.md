# Research Workspace

Documentation and templates for the open-source model research track.

## Contents

| File | Purpose |
|------|---------|
| `paper-outline.md` | Technical report structure and section ownership |
| `experiment-log.md` | Chronological log of fine-tuning experiments |
| `model-card-template.md` | Template for Hugging Face model cards (per-agent adapter) |
| `dataset-card-template.md` | Template for dataset cards (eval dataset release) |
| `benchmark-protocol.md` | Reproducibility protocol for external benchmark runs |

## Research Track Goals

1. Fine-tune Qwen 2.5 7B/14B adapters per agent using QLoRA (unsloth).
2. Evaluate on DealHunter golden datasets; publish results + adapters.
3. Release 20% of eval golden examples as a public benchmark sample.
4. Publish a technical report with methodology, results, and limitations.

See ADR-0009 (model strategy), ADR-0010 (eval harness), ADR-0011 (dataset pipeline), ADR-0012 (publishing strategy).

## unsloth Variant

Using `unsloth` base (not `unsloth[cu124]` or `unsloth[colab]`). Variant
selection documented in this README to centralise the decision. Switch to a
CUDA-pinned variant if the base package install fails on the target GPU environment.
