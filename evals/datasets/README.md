# Golden Datasets

One JSONL file per agent. Each line is a self-contained evaluation example.

## Schema

```json
{
  "id": "planner-0001",
  "agent": "planner",
  "input": {
    "user_message": "...",
    "context": {}
  },
  "expected": {
    "output_contains": ["keyword1", "keyword2"],
    "structured_fields": {}
  },
  "metadata": {
    "difficulty": "easy|medium|hard",
    "category": "ambiguity|multi-leg|budget-constrained|...",
    "source": "synthetic|human-reviewed",
    "reviewed_by": "human|null",
    "created": "2026-01-01"
  }
}
```

## Files

| File | Agent | Examples | Status |
|------|-------|----------|--------|
| `planner.jsonl` | planner | 0 | Phase 3.5 |
| `flight_hunter.jsonl` | flight_hunter | 0 | Phase 3.5 |
| `hotel_hunter.jsonl` | hotel_hunter | 0 | Phase 3.5 |
| `optimizer.jsonl` | optimizer | 0 | Phase 3.5 |
| `booking.jsonl` | booking | 0 | Phase 3.5 |
| `conversation.jsonl` | conversation | 0 | Phase 3.5 |

Target: 1,000 training + 100 eval examples per agent (see ADR-0011).
20% of eval examples will be released as the public benchmark sample.
