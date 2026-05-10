# Benchmark Reproducibility Protocol

Defines the exact procedure for reproducing the DealHunter benchmark results.

## Important: Use the Full Public Sample

Reproducers **must** run the benchmark on the **complete 20% public eval
sample** — all examples in `evals/datasets/` as released on Hugging Face.
Do **not** subsample, filter, or run on a random subset. Results derived from
fewer examples will have wider confidence intervals and are not comparable to
reported numbers.

Published results in the technical report use the full 600-example internal
eval set (100 per agent). External reproducers use the 120-example public sample
(20 per agent). This difference is documented in the paper's Limitations section.

## Prerequisites

```bash
# 1. Clone the repo at the tagged release
git clone https://github.com/gaurav-gandhi-2411/dealhunter
git checkout <release-tag>

# 2. Install dependencies
make setup

# 3. Pull eval dataset from Hugging Face
#    (command finalised in Phase 6.7)
huggingface-cli download dealhunter/eval-dataset --local-dir evals/datasets/

# 4. Download adapter weights
#    (command finalised in Phase 6.7)
huggingface-cli download dealhunter/<agent>-adapter --local-dir models/<agent>/
```

## Running the Benchmark

```bash
# Run full eval on public sample (all agents, all 120 examples)
LLM_ROUTING_PROFILE=local make eval-full
```

This produces result JSON in `evals/results/` and a summary CSV in
`evals/results/summaries/`.

## Reporting Requirements

When publishing results derived from this benchmark, you must report:

1. **Exact sample size** used (must equal total public eval sample — 20 per agent).
2. **Point estimate and confidence interval** for each metric (95% CI using bootstrap resampling with ≥1000 iterations).
3. **Git SHA or release tag** of the harness used.
4. **Adapter version** (Hugging Face model card commit hash).
5. **Hardware and runtime** used.

Omitting confidence intervals or running on a subset does not constitute a
valid reproduction of the published benchmark.

## Confidence Interval Computation

```python
import numpy as np

def bootstrap_ci(scores: list[float], n_boot: int = 1000, alpha: float = 0.05) -> tuple[float, float]:
    """95% CI via percentile bootstrap."""
    boot_means = [np.mean(np.random.choice(scores, size=len(scores), replace=True)) for _ in range(n_boot)]
    lo = np.percentile(boot_means, 100 * alpha / 2)
    hi = np.percentile(boot_means, 100 * (1 - alpha / 2))
    return float(lo), float(hi)
```

## Benchmark Registration (Optional)

Results may be submitted to the DealHunter leaderboard (Phase 11.5) once open.
Submit `evals/results/summaries/<run>.csv` with the metadata above.
