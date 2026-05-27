# Project Spec: Phase 2C.4 PR 3 completion + Phase 2C.4.5 prompt caching

## Goal

Two sequential pieces of work in one iteration:

**Part A — Phase 2C.4 PR 3 completion.** A frontend chat UI for conversational refinement is partially built (uncommitted code on branch `feat/<chat-ui-branch>` or similar — orchestrator will discover branch name via `git branch`). Code is written, lint and typecheck clean, but the 7-scenario browser verification was not completed. This iteration finishes the verification, opens the PR, and merges it.

**Part B — Phase 2C.4.5 prompt caching.** After Part A merges, add explicit Anthropic prompt caching to the three Haiku-running agents (Planner, Optimizer, ConversationManager) with proper measurement. This is documented in detail in the prior consulting session and the design is locked. Goal is 60-80% cost reduction on paid Haiku calls within the 5-min TTL window.

This iteration closes Phase 2C.4 entirely (the conversational agent feature) and ships Phase 2C.4.5 (the cost-discipline payoff on the paid path). After this iteration, the next spec covers Phase 2D backlog (issues #14, #15, #20, #21, #29, #30).

## Current state

See `CURRENT_STATE.md` for non-obvious context. Orchestrator MUST read this before planning. Critical items:

- The repo is mid-flight on PR 3 (frontend chat UI). An uncommitted branch contains chat-types.ts, ChatMessage.tsx, ChatLog.tsx, DemoClient.tsx changes, and event-map.ts cleanup. Discover state via `git status` and `git branch` first.
- The backend route at `apps/api/src/travel_agent/api/routes/refine.py` already emits the three new SSE events (PR #27 merged at 96dc59a). Don't modify backend code in Part A.
- Phase 2C.4.5 has been pre-designed in a prior consulting session. The design is locked: Anthropic-only, 5-min TTL, single breakpoint per agent, measurement via cache_creation/cache_read tokens.

## Scope

### In scope (this iteration)

**Part A — PR 3 completion:**
- Run the 7-scenario browser verification against `http://localhost:3000/demo` with staging backend (`.env.local` already points there)
- If scenarios pass: open PR, verify CI green, merge with squash, confirm staging deploy succeeds
- If any scenario fails: diagnose, fix on the same branch, re-verify, then proceed to merge
- Delete the dead `refine_started` event handler (CC noted this was done; verify it's still removed)

**Part B — Phase 2C.4.5 prompt caching:**

*Thread 1 — Verification audit (must run before Thread 2):*
- Inventory existing `cache_control` references in `apps/api/src/travel_agent/llm/anthropic.py` and any agent code
- Audit for cache-busters in PlannerAgent, OptimizerAgent, ConversationManagerAgent system prompts:
  - Timestamp/date injection in system prompt
  - Dynamic content before user message
  - Tool schema drift between calls
  - Temperature/thinking-mode variations
- Count tokens on each agent's static prefix using Anthropic's `client.messages.count_tokens()` to verify ≥1024 token minimum
- Document findings; file Phase 2D issues for problems requiring larger refactor

*Thread 2 — Implementation (only if Thread 1 audit is clean or fixable):*
- Update Anthropic adapter to accept `cache_control_breakpoint: bool` parameter
- When True, restructure `system` parameter as list with `cache_control: {"type": "ephemeral"}` block
- Surface `cache_creation_input_tokens` and `cache_read_input_tokens` from response.usage
- Update PlannerAgent, OptimizerAgent, ConversationManagerAgent to pass `cache_control_breakpoint=True` when routing to Anthropic profile
- Update `pricing.py` with separate rates: cache_creation = 1.25× input, cache_read = 0.10× input
- Update scorer to surface cache hit rate per agent per run
- Add WARNING log if hit rate < 50% on a run that should have caching (silent breakage signal)

*End-to-end verification (Part B):*
- Two-call sequence: same /search request twice, confirm cache_read_input_tokens > 0 on second call
- Same for /refine

### Out of scope (do not build)

- Multi-breakpoint cache patterns (single breakpoint per agent)
- 1-hour TTL (use default 5-min)
- Groq or NIM caching modifications (Groq auto-caches transparently for supported models; NIM has no caching)
- Modifying agent system prompts to artificially exceed 1024 tokens (file as Phase 2D issue if any agent is below)
- Multi-turn conversation memory (Level 3 ConversationManager feature, Phase 2D+)
- Production deploys (staging only; production requires manual approval)
- Any Phase 2D issue fixes (#14, #15, #20, #21, #29, #30) — separate iteration
- Phase 3 work (real hotel data, BookingAgent, multi-tenancy)
- Frontend changes beyond Part A's already-uncommitted work
- Rewriting or "improving" existing prompts during the audit — fix cache-busters only

## Tech stack

Only what's relevant to this iteration:

- Python 3.12 (backend)
- Pydantic 2.x (validation)
- anthropic SDK (latest; check `pyproject.toml` for pinned version)
- pytest, ruff, mypy (existing test/lint/type tools)
- Next.js 15, React 19, TypeScript (frontend, Part A only)
- Tailwind CSS (existing pattern)

No new dependencies expected. If anything needs adding, escalate.

## Architecture

Only NEW or MODIFIED files this iteration touches:

```
apps/
├── api/
│   ├── src/travel_agent/
│   │   ├── llm/
│   │   │   └── anthropic.py                              # MODIFIED: cache_control_breakpoint param + usage parsing
│   │   ├── agents/
│   │   │   ├── planner.py                                # MODIFIED: pass cache_control_breakpoint=True
│   │   │   ├── optimizer.py                              # MODIFIED: same
│   │   │   └── conversation_manager.py                   # MODIFIED: same (if prefix ≥ 1024 tokens)
│   │   └── observability/
│   │       └── pricing.py                                # MODIFIED: cache_creation/cache_read rates
│   ├── evals/optimizer/
│   │   └── scorer.py                                     # MODIFIED: cache hit rate surfacing
│   └── tests/unit/
│       └── llm/
│           └── test_anthropic.py                         # MODIFIED: cache tests
└── web/                                                  # Part A only
    ├── components/demo/
    │   ├── ChatMessage.tsx                               # NEW (already on branch)
    │   ├── ChatLog.tsx                                   # NEW (already on branch)
    │   └── DemoClient.tsx                                # MODIFIED (already on branch)
    └── lib/
        ├── chat-types.ts                                 # NEW (already on branch)
        └── event-map.ts                                  # MODIFIED (already on branch — refine_started removed)

docs/architecture/adr/
└── 0020-prompt-caching.md                                # NEW: ADR for Phase 2C.4.5 design
```

## Verification commands

```yaml
- name: backend-tests
  cmd: cd apps/api && pytest -v
  required: true
- name: backend-lint
  cmd: cd apps/api && ruff check .
  required: true
- name: backend-types
  cmd: cd apps/api && mypy src
  required: true
- name: frontend-lint
  cmd: cd apps/web && npm run lint
  required: true
- name: frontend-typecheck
  cmd: cd apps/web && npm run typecheck
  required: true
- name: coverage-gate
  cmd: cd apps/api && pytest --cov=src --cov-fail-under=80
  required: true
```

Pre-commit hooks must pass locally before any push.

## Subagent usage rules

- Use `executor` for code writing, file edits, audit findings documentation
- Use `verifier` for running tests/lint/types (via the Verification commands above)
- For Part A's 7-scenario browser verification: this requires browser interaction. The orchestrator cannot run a browser. Either escalate this to the user for manual execution, OR delegate to executor with curl-based SSE verification against staging as a substitute.

## Escalation rules (orchestrator MUST ask before doing)

- Ask before installing any new dependency (none expected; if needed, escalate)
- Ask if Thread 1 audit finds a cache-buster requiring larger refactor than a single-file change
- Ask if any agent's prefix is below 1024 tokens — orchestrator decides whether to file as Phase 2D issue or proceed without caching that agent
- Ask if Part A 7-scenario verification reveals broken UX (flicker, wrong scroll, robotic args_summary phrasing)
- Ask if any existing test newly fails after Part A merge or Part B changes
- Ask if any existing function signature would change (the Anthropic adapter's `chat()` method gets a new parameter — this is a signature change; document as `**kwargs` if it would break callers)
- Ask if verification fails 3 times in a row on the same check
- Ask before triggering production deploys (staging is automatic; production requires explicit user approval)
- Ask if a single executor pass would touch more than 6 files
- Ask if the audit suggests modifying agent system prompts beyond cache-buster fixes

## Hard rules (DO NOT touch)

- `apps/api/src/travel_agent/api/routes/refine.py` — backend wiring locked, PR #27 merged
- `apps/api/src/travel_agent/api/routes/search.py` — production search endpoint
- `apps/api/config/llm_routing.yaml` profile *contents* — profile YAML is load-bearing; changing model IDs, providers, or temperature requires explicit ADR
- The 4 demo profile names: `demo-haiku`, `demo-llama`, `demo-deepseek-v4`, `demo-gpt-oss-120b` — don't rename
- `docs/architecture/adr/0008-multi-provider-llm-abstraction.md`, `0016-llm-judge-design.md`, `0019-conversation-manager-agent.md` — read-only references, don't modify; create new ADRs for new decisions
- `apps/api/evals/optimizer/thresholds.py` values — don't relax thresholds to make failing evals pass
- `apps/api/src/travel_agent/coordinator/streaming.py` event types — don't add or rename SSE event types in this iteration
- `.github/workflows/deploy-staging.yml` and `deploy-production.yml` — CI/CD is load-bearing
- The PR #27 staging deploy commit: 96dc59a — this is the canonical "Phase 2C.4 backend complete" reference point
- `apps/api/src/travel_agent/agents/prompts/conversation_manager_system.txt` — system prompt was iterated to produce natural args_summary text; don't regenerate
- `apps/api/src/travel_agent/agents/optimizer.py` system prompt — has the explicit departure-time hallucination constraint; don't regenerate

## Budget

- **Soft target:** 1 full Max plan 5-hour window for Part A + Part B combined
- **Hard cap:** stop and escalate if executor invocations exceed 25 across the full iteration
- **Cost check:** orchestrator runs `/cost` at end of Part A (before starting Part B) and reports

Part A is small (~3-5 executor invocations expected, mostly verification + merge ceremony).
Part B is the meatier portion (~10-15 executor invocations, mostly adapter changes + tests + audit documentation).

## Success criteria (orchestrator verifies ALL before declaring done)

**Part A:**
- [ ] 7-scenario verification passes (or curl-equivalent verification if browser unavailable to subagents — explicit list of curls in PR description)
- [ ] PR opened with title `feat(web): chat-style refinement UI (Phase 2C.4 PR 3/3)`
- [ ] CI green on PR (API, Web, Eval quick, Detect secrets)
- [ ] PR merged with squash, branch deleted
- [ ] `Deploy — Staging` workflow runs on merge commit AND succeeds (not skipped due to `[skip ci]` inheritance — verify explicitly)
- [ ] Staging health check returns OK and the new chat UI renders correctly when manually loaded at `http://localhost:3000/demo` pointing at staging backend

**Part B:**
- [ ] ADR-0020 written documenting prompt caching design decisions
- [ ] Audit findings table in ADR-0020 or PR description: per-agent token counts, per-agent cache-buster findings, per-agent disposition (cached / skipped / fixed)
- [ ] Anthropic adapter accepts `cache_control_breakpoint` parameter
- [ ] At least one Haiku-routed agent (PlannerAgent or OptimizerAgent) confirmed cached via 2-call sequence — first call shows cache_creation_input_tokens > 0, second call shows cache_read_input_tokens > 0
- [ ] Scorer output displays cache hit rate (sample shown in PR)
- [ ] pricing.py rates cover cache_creation (1.25×) and cache_read (0.10×)
- [ ] Coverage stays ≥ 80% (currently 86%)
- [ ] ruff + mypy clean
- [ ] Frontend lint + typecheck clean (Part A)
- [ ] PR opened, merged, staging deploys green on merge commit
- [ ] Phase 2D issues filed for any audit findings requiring larger refactor

**Both parts:**
- [ ] No production deploys triggered
- [ ] No Anthropic API key (`ANTHROPIC_API_KEY` env var) set — Max plan billing preserved
- [ ] All commits pass pre-commit hooks
- [ ] No load-bearing file from Hard Rules section modified

## Build order (recommended)

**Part A (PR 3 completion):**
1. Run `git status` and `git branch` to discover the in-flight branch
2. Run frontend lint + typecheck to confirm clean state
3. Run 7-scenario verification (delegate to user as escalation, or curl-based equivalent if possible)
4. Open PR with description matching PR #27's pattern
5. Wait for CI green; merge with squash
6. Verify staging deploy succeeds on the merge commit
7. Report Part A complete

**Part B (Phase 2C.4.5):**
1. Read `https://platform.claude.com/docs/en/build-with-claude/prompt-caching` documentation (delegate to executor with the URL — they can webfetch if their environment allows, otherwise summarize from working knowledge and note any gaps)
2. Write ADR-0020 with the locked design decisions (Anthropic-only, 5-min TTL, single breakpoint, measurement strategy)
3. Thread 1 audit: inventory + cache-busters + token counts; report findings before any code change
4. Thread 2 implementation: adapter changes → unit tests → agent wiring → pricing.py → scorer.py
5. End-to-end 2-call verification (cache_creation then cache_read confirmed via response.usage)
6. Open PR, verify CI green, merge with squash
7. Verify staging deploy succeeds; report final cache hit rate from a fresh staging trace
8. Report Part B complete; iteration done

## Notes for the orchestrator

- The Max plan covers Opus 4.7 (orchestrator) and Sonnet (executor/verifier) — never set `ANTHROPIC_API_KEY` env var as this bypasses Max into pay-per-token billing
- `[skip ci]` discipline: avoid using this in any commit message in this iteration; in PR descriptions document explicitly that the merge commit must deploy
- This is an existing project with significant prior art. When in doubt, ask. Don't guess at conventions — they're documented in CURRENT_STATE.md or available via `git log -p` on prior similar work
- Phase 2C.4.5's prompt-caching prior design was discussed in the prior consulting session. Treat the design as the locked default; only escalate if the audit findings suggest the design needs adjustment
