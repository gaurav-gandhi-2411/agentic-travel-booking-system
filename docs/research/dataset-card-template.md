# Dataset Card — DealHunter Eval Dataset (Public Sample)

## Dataset Details

| Field | Value |
|-------|-------|
| **Name** | DealHunter Golden Eval Dataset — Public 20% Sample |
| **License** | CC-BY-4.0 |
| **Size** | ~120 examples (20 per agent × 6 agents) |
| **Full dataset** | 600 examples total (80% proprietary, not released) |
| **Format** | JSONL |
| **Languages** | English |
| **Domain** | Agentic travel booking |

## Motivation

Released to enable external reproducibility of published benchmark results.
The 20% public sample is a stratified random draw from the full eval set,
preserving the same difficulty and category distribution.

External reproducers working from this sample may observe results that differ
from those reported in the technical report, which uses the full 600-example
eval set. See `docs/research/benchmark-protocol.md` for the correct comparison procedure.

## Dataset Structure

Each JSONL line:

```json
{
  "id": "planner-eval-0001",
  "agent": "planner",
  "input": { "user_message": "...", "context": {} },
  "expected": { "output_contains": [...], "structured_fields": {} },
  "metadata": {
    "difficulty": "medium",
    "category": "multi-city",
    "source": "synthetic",
    "reviewed_by": "human"
  }
}
```

## Agents Covered

planner, flight_hunter, hotel_hunter, optimizer, booking, conversation

## Limitations

- Synthetic data with human QA; not drawn from real bookings
- English only; no multilingual coverage
- 80% of eval examples are withheld; published benchmark numbers use the full set
- Travel domain only; not suitable for general NLU benchmarks

## Citation

```bibtex
@dataset{dealhunter-eval-2026,
  title  = {DealHunter Golden Eval Dataset — Public Sample},
  author = {[Authors]},
  year   = {2026},
  url    = {[HuggingFace URL]},
  license = {CC-BY-4.0}
}
```
