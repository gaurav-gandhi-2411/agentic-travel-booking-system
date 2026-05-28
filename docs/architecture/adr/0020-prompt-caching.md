# ADR-0020 — Prompt Caching Strategy (Phase 2C.4.5)

## Context

Phase 2C.4.5 planned to add Anthropic prompt caching to PlannerAgent, OptimizerAgent, and
ConversationManagerAgent to reduce costs on the `demo-haiku` production profile.

A Thread 1 audit on 2026-05-29 found that the caching infrastructure was already partially
built (AnthropicAdapter supports `cache_system_prompt=True`, LLMResponse carries cache token
fields, and pricing.py handles cache rates). Planner and Optimizer already pass the kwarg.
What remained was wiring ConversationManager, correcting wrong documentation, and adding
observability to the eval scorer.

The audit also uncovered a documentation error in the codebase: comments in `planner.py` and
`optimizer.py` cited 1,024 tokens as the Haiku cache threshold. The actual threshold per the
Anthropic docs is **4,096 tokens for claude-haiku-4-5-20251001**. The Sonnet 4.6 threshold
is 1,024 tokens.

**Measured combined token counts** (system prompt + tool schema, character-based approximation
±15%):

| Agent | System | Tools | Combined | Sonnet 4.6 (≥1,024) | Haiku 4.5 (≥4,096) |
|---|---|---|---|---|---|
| Planner | ~777 | ~674 | ~1,451 | ✅ active | ❌ no-op |
| Optimizer `_explain` | ~362 | ~174 | ~536 | ❌ no-op | ❌ no-op |
| Optimizer `_compare` | ~362 | ~296 | ~658 | ❌ no-op | ❌ no-op |
| ConversationManager | ~967 | ~1,075 | ~2,042 | ✅ active (once wired) | ❌ no-op |

The cached prefix order is **tools → system → messages**, and the minimum token count applies
to the combined (tools + system) prefix, not system alone.

**Cache-buster audit** — all agents are clean:
- Planner and Optimizer inject `{today}` into their system prompts via `_load_system_prompt()`.
  This resolves once per calendar day. Within a 5-minute TTL window the resolved prompt is
  byte-for-byte stable. Not a cache buster.
- ConversationManager has a fully static system prompt (module-level constant, no injection).
  This is the cleanest cache target in the system.
- `tool_choice` is never set explicitly in the Anthropic adapter or in any of the three agents.
  The API default applies on all calls. Not a cache buster.
- No image content in any agent. Not relevant to caching.

## Decision

1. **Wire `cache_system_prompt=True` on `ConversationManagerAgent.understand()`** (Phase 2C.4.5).
   This is the only agent with an unwired cache kwarg. It qualifies on Sonnet 4.6 (2,042 tokens
   > 1,024 threshold) and has the cleanest prefix (no dynamic injection, no daily staleness).

2. **Retain `cache_system_prompt=True` on Planner and Optimizer** as-is. Planner caches on
   Sonnet 4.6 (1,451 > 1,024). Optimizer is a no-op on all profiles (536/658 < 1,024) but
   stays wired for future prompt growth.

3. **Correct documentation errors**:
   - `planner.py` comment: 1,024 threshold → 4,096 (Haiku); accurate combined token count
   - `optimizer.py` comments: same correction; note no-op on Sonnet 4.6 as well
   - `planner_system.txt` header: `~420 tokens` → `~777 (system only); ~1,451 combined`
   - `optimizer_system.txt` header: `~220 tokens` → `~362 (system only); ~536/~658 combined`

4. **Do not pad system prompts** to artificially reach the 4,096-token Haiku threshold. Prompt
   content decisions must be driven by quality needs, not cache eligibility. All three agents are
   well below the Haiku threshold — Planner at 35% (1,451/4,096), ConversationManager at 50%
   (2,042/4,096). Expanding to reach the threshold would more than double the prompt length.

5. **Use `cache_system_prompt` as the kwarg name** — pre-existing convention at all call sites.
   The spec proposed `cache_control_breakpoint`; renaming would touch working callers for no
   functional benefit.

6. **Add cache hit rate columns to the optimizer scorer** (informational, not a gate). Adds
   `CacheTrackingLLMClient` wrapper in the runner to capture per-scenario cache token totals.
   Scorer prints: `Cache: N writes / N reads | hit rate: N%`.

## Consequences

**On Sonnet 4.6 eval profile (manual baseline runs only):**
- Planner: caching active. System prompt `{today}` is date-stable within any session.
- ConversationManager: caching active after this iteration (fully static prefix).
- Optimizer: caching no-op on all profiles. Below threshold even on Sonnet 4.6.

**On Haiku 4.5 `demo-haiku` / `prod` profiles:**
- All three agents: caching is a no-op. None reach the 4,096-token threshold.
- **Zero cost reduction on the production demo profile in this iteration.**

**Future activation path (no code changes required):**
- When any agent's combined prefix naturally reaches the relevant threshold, caching activates
  automatically. The wiring is in place.
- Natural growth paths: few-shot examples for quality (Planner, Optimizer), Level-3 multi-turn
  conversation memory (ConversationManager — planned for Phase 2D+).
- Once ConversationManager's prompt grows past 4,096 tokens (via Level-3 memory or richer
  context), it will reach the Haiku threshold organically.

**Phase 2D follow-up issues filed:**
- Planner below Haiku 4.5 threshold (1,451 vs 4,096)
- Optimizer below all thresholds (536–658 tokens)
- ConversationManager below Haiku 4.5 threshold (2,042 vs 4,096)

## Alternatives Considered

- **Pad prompts to 4,096 tokens:** Rejected. The Anthropic docs note that expanding prompts
  to reach threshold is "often worthwhile" when the prompt is *just short*. These cases are not
  "just short" — the closest agent (ConversationManager) is at 50% of the Haiku threshold.
  Padding for padding's sake degrades clarity and couples prompt engineering to infrastructure
  thresholds.

- **Cache tool definitions separately** (tools with their own `cache_control` block): Rejected
  for this iteration. The single-breakpoint pattern (cache on system only) is simpler. Tool
  schemas are stable across calls so separate caching would help, but the combined approach
  (same breakpoint covers both) is already implemented. Adding a second breakpoint is Phase 2D+.

- **Skip wiring entirely until prompts reach threshold:** Rejected. Wiring now costs one kwarg
  and one comment per agent. Deferring means a future PR opens just for a kwarg.

- **Rename `cache_system_prompt` to `cache_control_breakpoint`:** Rejected. The existing kwarg
  name is used consistently at Planner and Optimizer call sites. Renaming adds churn for no
  functional benefit and the spec's naming was written before the existing code existed.

## References

- Anthropic prompt caching docs: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
  (verified 2026-05-29)
  - Minimum cacheable prefix: **4,096 tokens** for Claude Haiku 4.5; **1,024 tokens** for
    Claude Sonnet 4.6 and Sonnet 4.5
  - Cache prefix order: **tools → system → messages**
  - Threshold applies to the combined (tools + system) prefix when `cache_control` is placed
    on the system prompt
  - `tool_choice` changes and image presence both invalidate the messages cache
- ADR-0008 — multi-provider LLM abstraction (adapter interface design)
- ADR-0019 — ConversationManagerAgent design (Level 2 scope)
