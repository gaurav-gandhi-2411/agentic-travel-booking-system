# DealHunter — Value Proposition

**Audience:** Platform engineering leads evaluating the agent layer

---

## What we are

DealHunter is a multi-agent reasoning layer that sits in front of your existing travel
inventory. You provide flights and hotels via the adapter pattern; we provide the agent
that turns a natural-language query into a ranked, explained recommendation — and refines
it conversationally across multiple turns.

We are not an aggregator. You already have inventory. What you don't have is the agent.

---

## Four claims, each backed by a verifiable artifact

### 1. Window optimization that beats cheapest-first

DealHunter identifies the best 7-day travel window across a 30-day horizon using a
hierarchical sampling algorithm. Stage 1 sweeps 8 candidate windows with lightweight
provider calls; Stage 2 drills into the top 3; the Pareto frontier extracts two archetype
winners — best-value and best-experience — with natural-language explanations. Returning
the cheapest option for a fixed date is not this.

**Artifact:** ADR-0005 documents the algorithm rationale and sampling parameters.
Phase 3.5 eval results show recommendation quality vs a brute-force baseline across
2,304 diversity-matrix seeds.

### 2. Fine-tuned open-source models, published benchmarks

The agent ships on fine-tuned Qwen 2.5 7B/14B models benchmarked against Qwen 2.5 72B
and Llama 3.3 70B. Per-agent acceptance thresholds are defined before training begins
(ADR-0009); results are published win/loss numbers, not marketing claims. Agents that
don't reach threshold ship on the 70B fallback — that outcome is documented, not hidden.

**Artifact:** LoRA adapters on Hugging Face Hub (CC-BY-NC-4.0, PEFT format). Per-agent
eval results in `evals/results/`. Reproducible: `make eval` against published checkpoints
and published 20% eval sample returns reported numbers ±2%.

### 3. Production-grade SDK posture before business logic ships

The codebase reaches 40+ engineering-discipline commits before the first agent business
logic lands: multi-tenant Postgres RLS, AES-256-GCM credential encryption per ADR-0007,
OpenTelemetry tracing, WIF-based CI/CD, load-tested to 50 concurrent users. Every
load-bearing decision has a written ADR with rationale, alternatives considered, and
consequences. The repo is designed to survive a tech-lead review on day one of
procurement.

**Artifact:** ADRs 0001–0014 in `docs/architecture/adr/`. Runbooks for the top 5
incidents in `docs/runbooks/`. OpenAPI spec at `/docs`. Load test report in
`docs/performance/`.

### 4. Conversational refinement that re-enters at the right phase

"Cheaper" triggers a window re-search, not a full pipeline restart. "Different hotel
area" triggers a hotel re-rank against the existing flight set. "Skip red-eyes" applies
a departure-time filter to the flight query and re-runs only from FlightHunter forward.
The ConversationManagerAgent parses the refinement type and re-enters the coordinator at
the correct stage — no wasted provider calls, no context loss across turns.

**Artifact:** `ConversationManagerAgent` routing logic in
`apps/api/src/travel_agent/agents/conversation.py`. Pairwise judge prompt in
`evals/judges/conversation.txt` validates 3-turn coherence against the 70B baseline.

---

## How to evaluate

1. `make eval` against published HF checkpoints — reported numbers reproduce to ±2%.
2. Read ADRs 0001–0014. Every decision has rationale, alternatives, and consequences.
3. Request a sandbox API key — the demo tenant runs the full pipeline with disclosed
   synthetic inventory.
