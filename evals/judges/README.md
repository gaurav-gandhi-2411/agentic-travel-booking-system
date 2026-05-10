# Judge Prompts

LLM-as-judge configuration for pairwise preference evaluation.

## Judge Models

| Role | Model | Profile |
|------|-------|---------|
| Primary judge | Qwen 2.5 72B (OpenRouter free) | `free` |
| Cross-check judge | Llama 3.3 70B (OpenRouter free) | `free` |
| Frontier baseline | claude-sonnet-4-6 | `eval` (manual only) |

No frontier judge in CI (cost). See ADR-0010.

## Pairwise Protocol

Double-swap to reduce position bias:
1. Present (response_A, response_B) → judge picks winner
2. Present (response_B, response_A) → judge picks winner again
3. Score: A wins both = A preferred; B wins both = B preferred; split = tie

## Prompt Files

| File | Purpose |
|------|---------|
| `planner_judge.md` | Rubric for itinerary coherence, constraint satisfaction |
| `flight_hunter_judge.md` | Rubric for fare accuracy, option diversity |
| `hotel_hunter_judge.md` | Rubric for property matching, preference alignment |
| `optimizer_judge.md` | Rubric for multi-objective trade-off quality |
| `booking_judge.md` | Rubric for PNR correctness, confirmation completeness |
| `conversation_judge.md` | Rubric for tone, helpfulness, intent resolution |

All rubric files created in Phase 3.5.
