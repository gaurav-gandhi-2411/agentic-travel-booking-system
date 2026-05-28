# CURRENT_STATE.md — agentic-travel-booking-system

Context that isn't in the code. The orchestrator can read the repo for *what*; this doc explains *why*.

## Project goal

An agentic flight-search system that runs on free-tier LLM infrastructure to demonstrate cost-effective AI for B2B clients. Production target: 99% gross margin on the open-source profile, with paid Anthropic available as an opt-in premium tier. Coordinator pattern (deterministic Python orchestration), not autonomous multi-agent — Planner → FlightHunter → Optimizer pipeline with SSE streaming, plus a recently-added ConversationManagerAgent for natural-language refinement.

## Where to look first

Don't trust this document for repo structure — read these directly:

```bash
# Run these as your first orientation
git log --oneline -30
ls apps/api/src/travel_agent/
cat AUDIT_REPORT.md                    # original audit at project root
ls docs/architecture/adr/              # all ADRs, read 0019 most recently added
```

The repo has been actively developed across multiple phases. `git log` will show the recent arc better than I can describe it.

## Load-bearing files (don't casually touch)

These have hard-won design decisions baked in. Each has an ADR or a Phase document explaining why.

**`config/llm_routing.yaml`** — Defines the 4 active demo profiles (Haiku, Llama 3.3, DeepSeek V4 Flash, GPT-OSS-120B) plus the eval-judge profile. Profile-name changes cascade across the codebase. Adding a new profile requires the orchestration steps documented in ADR-0008 and ADR-0019.

**`apps/api/src/travel_agent/llm/__init__.py`** and the adapter files (`anthropic.py`, `groq.py`, `nvidia.py`) — The provider-agnostic LLM client interface. Critically, NIM uses an OpenAI-compatible transport (no separate SDK), Groq case-sensitivity quirks are handled at both schema and validator layers, and the `extra_params` plumbing is required for `reasoning_effort` on GPT-OSS-120B.

**`apps/api/src/travel_agent/evals/optimizer/thresholds.py`** — Gate values derived from canonical baseline runs. Per-provider completion thresholds (NVIDIA differs from Groq for credit-pool reasons). Changing these changes what "regression" means.

**`apps/api/src/travel_agent/agents/optimizer.py`** — The system prompt has an explicit constraint against citing departure times (Haiku was hallucinating them, Issue #14). Don't regenerate or "improve" this prompt without re-baselining all profiles against it.

**`apps/api/src/travel_agent/agents/conversation_manager.py`** + `conversation_manager_types.py` + `prompts/conversation_manager_system.txt` — Brand new (PR #25, PR #27). The args_summary field is LLM-generated and the prompt instructs natural-language phrasing — touch carefully. Locked at Level 2 ambition (single-turn understanding, no persistent memory beyond SearchCache TTL).

**`apps/api/src/travel_agent/evals/optimizer/runner.py`** — The `_PROFILES` default excludes Haiku (opt-in for cost discipline) and excludes NIM-hosted profiles (credit pool incompatible with nightly cadence). This is intentional. ThrottledLLMClient handles Groq TPM but NOT credit pools.

**`apps/api/src/travel_agent/observability/pricing.py`** — Per-model rates including NIM models at $0 and Groq judge at $0. Cost surfacing is wired through scorer; "Anthropic spend" appears on every eval run output with `!!` prefix when > 0.

**`.github/workflows/deploy-staging.yml`** — Real deploy on push to main. Production deploy requires manual approval (GitHub Environments). Workload Identity Federation, no service account keys.

## Non-obvious conventions

**LLM profile naming.** Profiles are named `demo-<short-name>` (e.g., `demo-llama`, `demo-gpt-oss-120b`). The "demo-" prefix means "production-facing demo profile" — these appear in the frontend selector. Eval-only profiles use `eval-judge-<name>`. Don't drop the prefix.

**Cost discipline rule.** Paid Anthropic spend must be visible. Any new agent or eval that touches `demo-haiku` should surface its expected cost in the PR description. Default profile lists should never include Haiku without explicit reasoning in the commit message.

**Eval gate philosophy.** Thresholds are set tight on purpose. If Llama completion is 87.5% and threshold is 0.83, that's a deliberate "one more failure surfaces the underlying issue" gate, not a margin for safety. Don't relax thresholds to make failing evals pass — fix the underlying issue or document it.

**Pydantic discipline.** Every LLM tool-use response goes through Pydantic models with `model_validator` for cross-field invariants. The ConversationManagerOutput uses exactly-one-of args validation. When adding new agent types, mirror this pattern — don't trust LLM output without schema validation.

**SearchCache shape.** Redis-backed, 30-min TTL, keyed by request_id, holds `(TravelIntent, list[FlightOption], optional archetypes)`. No Postgres for user data (Neon is provisioned but unused, deferred to Phase 3). Don't introduce a new persistence layer without ADR.

**SSE event ordering matters.** The frontend's `useSearchStream` hook accumulates events to state. New event types automatically reach the UI via graceful fallthrough — but the *order* of events is the protocol. `conversation_thinking` must always fire before `conversation_action_classified`. Tests verify this.

**Groq schema gotcha.** GPT-OSS-120B on Groq rejects lowercase enum values at the schema pre-validation layer (before request reaches Python). Llama 3.3 doesn't have this issue. Fix is two-layer: schema enum includes uppercase variants AND Pydantic field_validator normalizes case on response. See Issue #29.

**`[skip ci]` in commit messages is a footgun.** When squash-merging, `[skip ci]` in any branch commit inherits into the squash commit and suppresses all main-branch workflows including Deploy-Staging. Pattern hit us during PR #27 merge. See Issue #30.

**`.env` loading via `find_dotenv()`.** Scripts that load API keys must use `dotenv.find_dotenv()` rather than relative paths. Relative paths break depending on CWD (different scripts run from different directories). Some legacy scripts use relative paths; cleanup in docs/backlog.md.

**localStorage cleanup pattern.** The frontend `ProfileToggle.tsx` defensively clears stale profile IDs from localStorage on load. When changing the active profile set, update both the union type AND the localStorage guard's allowlist.

## Important decisions and the "why"

**Groq + NIM, not just one provider.** Groq has fast inference and daily-reset quotas; NIM has model variety and lifetime credit pool. Operationally different. We use Groq for nightly evals (resettable), NIM for opt-in demos (credit-pool aware). Phase 2C.2 substrate work documented this asymmetry in ADR-0008.

**Llama 3.3 as default for ConversationManagerAgent (not GPT-OSS-120B).** Phase 2C.4 PR 1 cross-profile eval showed Llama 100% action accuracy vs GPT-OSS 93.3%. GPT-OSS missed one borderline scenario (budget update — REFINE vs REPLAN). Llama is faster and matched accuracy on the classification task. See ADR-0019.

**GPT-OSS-120B at `reasoning_effort: low`.** Default `reasoning_effort: medium` produces ~500 hidden reasoning tokens, hitting max_tokens before tool response completes. Discovered during Phase 2C.3 baseline (4/24 truncation failures). The fix is in the `extra_params` on the profile. Don't remove this.

**Haiku-opt-in (not default).** Phase 2C.1 established empirical parity between Llama (free) and Haiku (paid) on coherence. Phase 2C.2 dropped Haiku from nightly default to enforce cost discipline. Re-introducing Haiku to nightly defaults requires explicit justification.

**Cache breakpoint placement (Phase 2C.4.5 prep).** Anthropic prompt caching is the next iteration's focus. The audit step is critical because Phase 2A noted "caching wired but no-op until prompts >1024 tokens." Don't assume existing caching is working — verify with the API's `cache_creation_input_tokens` and `cache_read_input_tokens` usage fields.

## Known issues and explored dead ends

**Phase 2D backlog (filed as GitHub issues):**
- #14 — Haiku departure-time hallucination (resolved in Phase 2C.2 prompt fix; left open for tracking)
- #15 — Llama eval bounded by Groq TPD; workarounds documented, not yet implemented
- #20 — Judge cache poisoned by failed-parse score=1 entries; recurs if evals are interrupted mid-run
- #21 — Cross-profile coherence requires consistent judge model (current evals mix Qwen3-32B and Sonnet)
- #29 — Groq schema enum case sensitivity differs between models
- #30 — `[skip ci]` in squash-merge silently suppresses deploys

**Dead ends already explored:**
- **NIM Qwen3.5-397B as 4th profile.** Failed at 14/24 completion due to NIM's 1000-credit lifetime pool. Documented and abandoned. Don't retry the same model on NIM unless NIM changes their tier model.
- **Increasing max_tokens for GPT-OSS-120B.** Made truncation *worse* (model used headroom for more hidden reasoning). The fix is `reasoning_effort: low`, not bigger budgets.
- **Per-second RPM throttle for NIM.** Built and tested; doesn't help because the underlying constraint is credit pool, not rate limit. Code remained for defense in depth; don't expect it to fix NIM completion issues.
- **Qwen3-32B as runtime profile.** Same model is used as eval judge; same-family bias would invalidate eval scores. Excluded from demo profile set deliberately.

**pip-audit workflow noise.** The pip-audit GitHub Actions workflow reports "0s failure" on every push due to path-filter quirks. Pre-existing, accepted, tracked as Issue #18. CI dashboard shows red ❌ next to pip-audit — this is noise, not a real failure.

## Tests / lint / types — current state

**As of PR #36 merge (commit 41e8e65) — Phase 2C.4.5 complete:**
- 458 tests passing, 85.97% coverage
- ruff check passing
- mypy passing (fixed pre-existing redis_cache.py type errors surfaced by stubs drift)
- Frontend: lint clean, typecheck clean

**Known-broken and accepted:**
- pip-audit workflow's 0s failures (Issue #18)
- pre-existing `find_dotenv()` inconsistency in eval scripts (docs/backlog.md)

## Open questions I'm flagging honestly

**I don't know:**
- Whether the `[skip ci]` footgun has been fully fixed since I last had context on it. Issue #30 was filed but I don't know if a guardrail was added.
- The exact state of `apps/api/docs/backlog.md` — it was created mid-session and may contain items not migrated to GitHub issues.
- Whether all the integration tests pass against the live staging deploy currently, or only against the mocked test fixtures.

## Repository orientation

```
agentic-travel-booking-system/
├── apps/
│   ├── api/                          # FastAPI backend (Python 3.12)
│   │   ├── src/travel_agent/         # Source code
│   │   │   ├── agents/               # PlannerAgent, OptimizerAgent, ConversationManagerAgent
│   │   │   ├── api/                  # FastAPI routes (search, refine, health)
│   │   │   ├── coordinator/          # Streaming pipeline, state management
│   │   │   ├── llm/                  # Provider adapters (anthropic, groq, nvidia)
│   │   │   ├── observability/        # Langfuse, pricing
│   │   │   └── evals/                # Eval harness for optimizer + conversation_manager
│   │   ├── tests/                    # 453 tests
│   │   ├── config/                   # llm_routing.yaml (LLM profiles)
│   │   └── docs/                     # backlog.md, design notes
│   └── web/                          # Next.js frontend (React 19)
│       ├── components/demo/          # DemoClient, ProfileToggle, ChatMessage, ChatLog
│       ├── hooks/                    # useSearchStream
│       └── lib/                      # event-map.ts, chat-types.ts
├── docs/architecture/adr/            # Architecture Decision Records, especially 0008, 0019
├── .github/workflows/                # CI, Deploy-Staging
├── AUDIT_REPORT.md                   # Original audit (Phase 1 baseline)
└── README.md
```

## What "ready to ship" looks like for any iteration

Across all phases of this project, the consistent definition has been:

1. All new code has Pydantic validation if it touches LLM output
2. All new agents have unit tests and an eval baseline
3. All new SSE events are documented in OpenAPI / FastAPI schema
4. Coverage stays at 80%+ (currently 86%)
5. ruff + mypy clean
6. Pre-commit hooks pass locally before push
7. PR description includes any baseline numbers, cost impact, and reference to relevant ADRs
8. Staging deploys green (`Deploy — Staging` workflow succeeds on the merge commit, not just the PR commit — `[skip ci]` traps disqualify this)
9. Production deploys require manual approval

The orchestrator should treat all 9 as non-negotiable for any iteration's success criteria.
