# ADR-0001: Multi-Agent Coordinator Pattern

**Status:** Accepted — 2026-05-09

---

## Context

DealHunter — now the Agentic Travel Booking System — needs to orchestrate six specialist
roles: parsing user intent, searching flights, searching hotels, optimizing across
results, managing the booking flow, and handling conversational refinement. Each of these
roles has different latency characteristics, different LLM requirements (some need Sonnet's
reasoning; others can run on Haiku), different failure modes, and different test surfaces.

The key design question is: how do these roles coordinate?

There are three broad patterns in the multi-agent literature:

1. **Deterministic coordinator** — a non-LLM orchestration layer dispatches to stateless
   specialist agents in a defined order, merges results, and manages control flow.
2. **Single-agent tool loop** — one LLM has all capabilities exposed as tools and decides
   what to do next at each step.
3. **Free-form multi-agent** — agents spawn other agents, send messages to each other, or
   vote on outcomes. The orchestrator is itself an LLM.

This system must also support:
- Parallel execution of FlightHunterAgent and HotelHunterAgent (they have no data
  dependency on each other within a given window).
- A strict call budget (150 flight calls, 100 hotel calls, 20 LLM calls per request).
- Testability at the agent level without standing up the full pipeline.
- Debuggable production traces — enterprise buyers will ask "why did it pick this window?"
- The booking flow (§7) requires a specific HITL state machine, not emergent behavior.

---

## Decision

We use a **deterministic coordinator** pattern. The `Coordinator` class is pure Python
code with no LLM calls. It dispatches to stateless specialist agents in a defined
sequence, manages parallelism, enforces call budgets, and merges results.

**Key structural rules:**

1. **Agents are stateless functions.** Each agent takes a `RequestState` (Pydantic model)
   and returns a mutated `RequestState`. No agent holds session state between calls.
   Signature pattern:
   ```python
   async def run(self, state: RequestState) -> RequestState: ...
   ```

2. **Agents never call other agents.** All composition happens in the coordinator. An
   agent can call providers (via the adapter layer) and Claude (via the Anthropic SDK),
   but it cannot call `PlannerAgent`, `OptimizerAgent`, etc. directly.

3. **The coordinator is the only dispatcher.** It decides what runs next based on
   deterministic logic (current phase, budget remaining, error state). No LLM decides
   the next step.

4. **The coordinator parallelizes.** Within the window search phase, it runs
   `FlightHunterAgent` and `HotelHunterAgent` as concurrent `asyncio` tasks per window.
   This is the primary latency optimization — the two searches are independent.

5. **WindowSearcher is coordinator code, not an agent.** It implements the hierarchical
   sampling algorithm (§5), dispatches to the hunter agents, and manages the call budget
   counter. It contains no LLM calls itself. (See also ADR-0005.)

6. **`RequestState` is the single source of truth.** It is a Pydantic model defined in
   `apps/api/src/travel_agent/coordinator/state.py` that accumulates all intermediate
   results: the parsed `TravelIntent`, candidate windows, collected `FlightOption[]` and
   `HotelOption[]`, Pareto frontier, selected archetypes, booking status, and error log.

**The coordinator dispatch sequence (happy path):**

```
1. PlannerAgent          → parses NL input → TravelIntent
2. WindowSearcher        → hierarchical sampling → FlightOption[], HotelOption[]
     ├── [parallel] FlightHunterAgent ×N windows
     └── [parallel] HotelHunterAgent ×N windows
3. OptimizerAgent        → Pareto frontier → two archetype packages + explanations
4. ConversationManager   → surfaces to user, handles refinement loop
5. BookingAgent          → HITL lock → confirm → audit log
```

On refinement ("cheaper", "no morning flights"), the coordinator receives a `RefineIntent`
from `ConversationManagerAgent` and re-enters at the appropriate step — typically
WindowSearcher (if dates change) or OptimizerAgent (if only filter constraints change),
skipping PlannerAgent re-invocation.

---

## Consequences

**Positive:**
- Coordinator is pure Python, fully unit-testable without mocking Claude. Given its
  critical role (it controls the entire pipeline), this test coverage is important.
- Agent failures are locally containable. If `HotelHunterAgent` for window 3 fails, the
  coordinator can log the failure, mark that window as partial, and continue.
- Parallelism is explicit and auditable — `asyncio.gather()` calls in coordinator code,
  not emergent from agent-to-agent messaging.
- The OTel trace structure matches the code structure: one span per coordinator step, one
  child span per agent invocation, one grandchild per provider call. Debugging is a
  matter of reading the trace.
- Adding a new agent role in a future phase is a new class + a new dispatch step in the
  coordinator. No existing agents are modified.
- The HITL booking state machine (ADR-0003, §7.2) is implementable as deterministic
  coordinator state, not as an emergent LLM behavior — a hard requirement for enterprise
  buyers who need auditable booking flows.

**Negative:**
- The coordinator is a new abstraction to understand. Engineers new to the codebase must
  understand the coordinator before they can reason about agent sequencing.
- Very dynamic requirements (e.g., "determine at runtime which agents to invoke based on
  user context") require coordinator changes, not just new agents. This is intentional
  rigidity, but it is rigidity.
- The coordinator pattern assumes the agent dispatch order is known at design time.
  Exploratory "what should I search next?" flows are harder to express (though not
  needed for v1).

**Neutral:**
- The coordinator has no LLM cost. All Claude costs are in the agents. This makes the
  cost model simple: cost = sum(agent costs), attributable by agent in the cost ledger.
- `RequestState` grows as phases are added. By Phase 6 it will contain ~15 fields.
  Pydantic v2 keeps this manageable; the model is defined once and shared.

---

## Alternatives Considered

### Alternative 1: Single-agent tool loop

One `claude-sonnet-4-6` instance with all capabilities as tools: `search_flights`,
`search_hotels`, `score_packages`, `lock_offer`, etc. Claude decides the order and
when to stop.

**Rejected because:**
- Cannot parallelize flight and hotel searches — the LLM calls them sequentially, adding
  ~10–15s of avoidable latency per window.
- The call budget (150 flight, 100 hotel, 20 LLM) is unenforced — Claude may decide to
  call `search_flights` 40 times if it thinks it's useful.
- The booking HITL state machine cannot be reliably expressed as tool-call behavior.
  We cannot guarantee that Claude will always call `lock_offer` before `confirm_booking`,
  or that it will surface the offer hold timer correctly.
- Agent evaluation (plan.md §12) requires deterministic input/output schemas. A tool
  loop produces outputs that depend on the LLM's sequencing decisions, making golden
  test suites brittle.
- The trace of "which tools did Claude call and why" is opaque relative to coordinator
  dispatch steps.

### Alternative 2: Free-form multi-agent (AutoGen / CrewAI style)

An LLM orchestrator decides which specialist agents to spawn and in what order. Agents
can message each other (e.g., `HotelHunterAgent` could message `FlightHunterAgent` to
coordinate on windows).

**Rejected because:**
- An LLM orchestrator making "which agent next?" decisions is an additional LLM call per
  decision, increasing cost by ~5–10 calls per request on top of the existing 20-call
  budget.
- Agent-to-agent messaging makes the state machine emergent. Debugging a production
  failure requires reconstructing the message history to understand what decision was made
  and why — far harder than reading a coordinator trace.
- The Pareto frontier and scoring logic (ADR-0006) require all flight+hotel results to be
  assembled before OptimizerAgent runs. Ensuring this ordering via agent messaging is
  complex; in the coordinator pattern it's a single `await asyncio.gather(...)`.
- Enterprise buyers evaluating this for procurement need to understand and sign off on the
  booking flow. "The LLM orchestrator decided" is not an acceptable answer for why a
  booking was or wasn't executed.
- Framework lock-in (AutoGen, CrewAI) vs. clean Python code with direct Anthropic SDK
  usage as specified in plan.md §9.

### Alternative 3: Hierarchical agents calling each other

The `CoordinatorAgent` (an LLM) calls `FlightHunterAgent`, which calls `AmadeusAdapter`
directly and returns to the coordinator. Similar to option 2 but agent-initiated rather
than orchestrator-spawned.

**Rejected for the same fundamental reasons as option 2.** Additionally, circular call
graphs become possible if agents are allowed to call each other, requiring dependency
analysis to prevent. The one-directional flow (coordinator → agents → adapters) enforced
by option 1 (our choice) eliminates this entire class of bug.

---

*Referenced plan.md sections: §4.1, §4.2, §5, §7.2, §9, §12*
