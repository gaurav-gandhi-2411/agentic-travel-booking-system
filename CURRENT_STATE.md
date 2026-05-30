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

**`.env` loading via `find_dotenv()`.** Scripts that load API keys must use `dotenv.find_dotenv()` rather than relative paths. Relative paths break depending on CWD (different scripts run from different directories). Some legacy scripts use relative paths; tracked as Issue #49.

**localStorage cleanup pattern.** The frontend `ProfileToggle.tsx` defensively clears stale profile IDs from localStorage on load. When changing the active profile set, update both the union type AND the localStorage guard's allowlist.

**Vercel deploy mechanism (confirmed Phase 2D iteration 4).** The Vercel GitHub integration IS linked (`.vercel/project.json` present) but does NOT auto-deploy to production. Production branch is `main`, but pushes to `main` do not trigger production deploys — all 14 production deploys occurred during the initial project setup burst (May 14-16). Use `vercel deploy --prod --archive=tgz` from the repo root to trigger production deploys via CLI (authenticated as `gaurav-gandhi-2411`). The `--archive=tgz` flag is required to stay under Vercel's 15,000-file limit (node_modules are on disk). Vercel project "Root Directory" is set to `apps/web/` in the dashboard — do NOT run `vercel` from `apps/web/` (it doubles the path and fails).

**Vercel CLI v54.0.0 preview env var bug.** `vercel env add NAME preview --value X --yes` returns `git_branch_required` even with the flags the CLI itself suggests. Preview-scoped env vars cannot be set non-interactively via CLI. Set them in the Vercel dashboard under Settings → Environment Variables → Preview → All Preview Branches. Production-scoped vars do NOT propagate to preview. Vercel env vars are baked at deploy time even for `force-dynamic` routes — a redeploy is required after any env var change.

## Important decisions and the "why"

**Groq + NIM, not just one provider.** Groq has fast inference and daily-reset quotas; NIM has model variety and lifetime credit pool. Operationally different. We use Groq for nightly evals (resettable), NIM for opt-in demos (credit-pool aware). Phase 2C.2 substrate work documented this asymmetry in ADR-0008.

**Llama 3.3 as default for ConversationManagerAgent (not GPT-OSS-120B).** Phase 2C.4 PR 1 cross-profile eval showed Llama 100% action accuracy vs GPT-OSS 93.3%. GPT-OSS missed one borderline scenario (budget update — REFINE vs REPLAN). Llama is faster and matched accuracy on the classification task. See ADR-0019.

**GPT-OSS-120B at `reasoning_effort: low`.** Default `reasoning_effort: medium` produces ~500 hidden reasoning tokens, hitting max_tokens before tool response completes. Discovered during Phase 2C.3 baseline (4/24 truncation failures). The fix is in the `extra_params` on the profile. Don't remove this.

**Haiku-opt-in (not default).** Phase 2C.1 established empirical parity between Llama (free) and Haiku (paid) on coherence. Phase 2C.2 dropped Haiku from nightly default to enforce cost discipline. Re-introducing Haiku to nightly defaults requires explicit justification.

**Cache breakpoint placement (Phase 2C.4.5 prep).** Anthropic prompt caching is the next iteration's focus. The audit step is critical because Phase 2A noted "caching wired but no-op until prompts >1024 tokens." Don't assume existing caching is working — verify with the API's `cache_creation_input_tokens` and `cache_read_input_tokens` usage fields.

**OpenRouter as free-routing primary.** `config/llm_routing.yaml` uses OpenRouter as the primary provider for the `free` routing profile (not just experimental scaffolding). `OPENROUTER_API_KEY` is bound to the prod service for on-demand activation via `LLM_ROUTING_PROFILE=free` header override. Currently prod runs `LLM_ROUTING_PROFILE=demo` so OpenRouter isn't invoked in normal traffic. Groq is the fallback.

## Production state (Phase 2D iteration 5 — 2026-05-31)

Both surfaces are functional. Backend is **1 non-critical commit behind** (see staleness note below); frontend is fully current. No application logic changed this iteration — this was CI/monitoring-only work.

### Backend (Cloud Run)

- **Running revision:** `agentic-travel-booking-api-prod-00016-rab` at 100% traffic
- **Image:** built from `main` HEAD at commit `3d30839` (PR #46 merge — deploy workflow canary gate fix)
- **Git equivalent:** 69 commits ahead of v0.5.0; deploy corresponds to v0.6.0 milestone
- **Staleness note (iteration 5):** Production is 1 commit behind main in `apps/api/` — the Part C housekeeping squash-merge deleted `apps/api/docs/backlog.md`. Non-functional change; no redeploy required before the next feature iteration. GitHub issue #51 (production-staleness-alert) will close automatically on next backend deploy.
- **Service URL:** `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`
- **Env bindings active:** `APP_ENV=production`, `UPSTASH_REDIS_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` — all bound and connected to running code
- **Deploy method:** `workflow_dispatch stage=canary` (Gate 1) → human smoke test → `workflow_dispatch stage=full` (Gate 2 after GG approval)
- **Post-promotion verified (iteration 3):** `/health` → `{"status":"ok","phase":"C","cache":"ok"}`, `/search` + `/refine` cache hit=true, all 4 profiles confirmed

See ADR-0023 for the full backend deploy narrative.

### Frontend (Vercel)

- **Production URL:** `https://agentic-travel-booking-system.vercel.app`
- **Deployment ID:** `dpl_F3DMy1YysATzBgWKBpd6RzoCvR85`
- **Git commit:** `1cf0a07` (current `main` HEAD, includes PRs #22 and #32)
- **Deployed:** 2026-05-31 via `vercel deploy --prod --archive=tgz` (Vercel CLI, authenticated as `gaurav-gandhi-2411`)
- **Env vars (Production scope):** `API_BASE_URL=https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app`, `DEMO_API_KEY` — both set correctly; prior deployment had both as empty strings (root cause of broken searches)
- **Post-deploy verified (orchestrator + GG browser visual):**
  - Bundle: `conversation_thinking`, `conversation_action_classified`, `args_summary`, all 4 chat kinds, all 4 profiles present; `demo-qwen` gone
  - `/api/search` → full SSE pipeline reaching prod Cloud Run (confirmed via `search_cache_put_success` in Cloud Run logs)
  - `/api/refine` → `conversation_thinking` → `conversation_action_classified` (action=refine, args_summary LLM-generated) → Redis cache hit → archetypes
  - GG browser: 4-profile selector, progress feed, archetype cards, chat bubbles (user/thinking/action/message), NO_OP conversation_message, zero console errors

## Production audit summary (Phase 2D iteration 2)

**Service URL (confirmed Phase 2D iteration 3):** `https://agentic-travel-booking-api-prod-rqyyasfwaa-el.a.run.app` — this is the canonical URL per `gcloud run services describe`. The `646079085526.asia-south1.run.app` URL cited in `spec.md` is stale.

**Pre-existing canary revision (Phase 2D iteration 3):** Revision `agentic-travel-booking-api-prod-00012-mab` carries the `canary` tag at 0% traffic. Created 2026-05-16 by the WIF SA (a prior deploy attempt). Running a pre-Phase-2B image (`sha256:ae4c359f`). Harmless — the new deploy will reassign the `canary` tag to the new revision.

**Finding (2026-05-30):** Production is frozen at `v0.5.0` (tagged 2026-05-15, commit `78c57db`), deployed before Phase 2B merged. The running image predates:
- `cache.py` / `redis_cache.py` — Redis cache doesn't exist in the image
- Langfuse observability bootstrap
- Structured JSON logging via structlog JSONRenderer
- All ConversationManagerAgent code (Phase 2C.4)
- All Phase 2D.1 cache observability log events

The `UPSTASH_REDIS_URL` secret was already present in the deploy workflow but production was never re-deployed after Phase 2B. The `UPSTASH_REDIS_TOKEN` GCP secret is vestigial — no code in any version reads it.

**Actions taken (2026-05-30):**
- GG applied env bindings manually to the prod Cloud Run service (revision `00011-2k8`): `APP_ENV=production`, `UPSTASH_REDIS_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`. Bindings are correct but **inert** against the v0.5.0 image.
- `deploy-prod.yml` updated to add `APP_ENV=production` to `env_vars:` block (PR for Phase 2D iteration 2). `UPSTASH_REDIS_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` were already present in the workflow. `UPSTASH_REDIS_TOKEN` intentionally not added (vestigial).
- Production deploy deferred to dedicated iteration — first prod deploy since May 15 is a ~3-phase delta requiring its own pre-flight checklist.

~~Production is **stale, not broken** — APP_MODE=demo, no active dependencies, minimal traffic. Issue #37 closed.~~ Resolved in iteration 3 (backend) and iteration 4 (frontend). Both now current.

## Production audit summary (Phase 2D iteration 4)

**Frontend freeze finding (2026-05-31):** The Vercel production deployment was frozen at commit `034bc03` ("chore: refresh contributor stats", 2026-05-16) — the same two-week freeze window as the backend's v0.5.0 freeze. The deployed code predated:

- **PR #22** (`eea11d3`) — 4-profile demo selector: replaced the old `demo-qwen` profile with `demo-gpt-oss-120b` and `demo-deepseek-v4`. The bundle showed only `demo-haiku`, `demo-llama`, `demo-qwen`.
- **PR #32** (`02f3345`) — Chat-style refinement UI: `ChatMessage.tsx`, `ChatLog.tsx`, `chat-types.ts`, and all `conversation_*` SSE event handling. The bundle had no trace of `ChatLog`, `conversation_thinking`, `conversation_action_classified`, or `args_summary`.

Confirmed via JS bundle inspection of the deployed chunks on the production domain.

**Empty env var finding (2026-05-31):** Both `API_BASE_URL` and `DEMO_API_KEY` had been set to **empty strings** (not null/undefined) in Vercel's production env since May 15. The Next.js API routes use `process.env.API_BASE_URL ?? 'http://localhost:8000'`. The `??` (nullish coalescing) operator only catches `null` and `undefined` — not empty string. So `apiBase = ""` and every `fetch("" + "/search")` call threw a URL parse error (`fetch failed`) at the Vercel serverless function layer, before any request reached the backend.

**Conclusion:** The production frontend had **never functioned for searches** since initial setup on May 15. The backend freeze (v0.5.0) and frontend freeze (034bc03 + empty env vars) together mean the production demo was fully non-functional from day one. Any demos would have been against local dev or staging environments.

**Actions taken (2026-05-31):**
- `API_BASE_URL` and `DEMO_API_KEY` set for Production scope via `vercel env add` (CLI). Previous empty-string entries removed first.
- `vercel deploy --prod --archive=tgz` triggered from repo root (current `main` HEAD, commit `1cf0a07`).
- New deployment `dpl_F3DMy1YysATzBgWKBpd6RzoCvR85` aliased to `agentic-travel-booking-system.vercel.app`.
- End-to-end verified: full SSE pipeline, ConversationManager REFINE path, Redis cache hit confirmed in Cloud Run logs for request_id `8bc9239d-...`, GG browser visual passed all 5 checks.

See ADR-0024 for the full frontend alignment narrative.

## CI/housekeeping summary (Phase 2D iteration 5 — 2026-05-31)

No application logic changed. Four CI/process-hygiene items:

**Part A — Production staleness guardrail (#45, closed):**
- `.github/workflows/production-staleness-check.yml` added: daily cron (04:00 UTC) + `workflow_dispatch` with `test_stale` input
- Backend check: WIF auth → `gcloud run revisions describe` → image digest (stripped to `sha256:HASH`) → Artifact Registry tag scan for 40-char SHA → `git rev-list --apps/api/` path-filter
- Frontend check: Vercel REST API (`VERCEL_TOKEN` repo secret) → `meta.githubCommitSha` → `git rev-list --apps/web/` path-filter
- Alert: single stable issue (`production-staleness-alert` label), updated in-place, auto-closes when both surfaces are current
- Live verified: backend correctly identified `3d30839`, frontend correctly identified `1cf0a07`; forced-stale test (`test_stale=true`) showed backend 45 behind + frontend 2 behind, issue #51 updated in-place
- See ADR-0025

**Part B — setup-node v4→v5 (#41, closed):** `actions/setup-node@v4 → @v5` in `ci.yml`. Web CI green on v5.

**Part C — backlog.md migration (#42, closed):** `apps/api/docs/backlog.md` had 1 item (BACK-001: find_dotenv cleanup) → migrated to Issue #49 → file deleted.

**Part D — startup-log renderer quirk (#47, closed):** `cache_backend_selected` logs as textPayload at worker startup because `_make_cache()` runs at import time (cache.py line 93) before `structlog.configure(JSONRenderer)` in main.py (line 33). Closed as low-priority-accepted — 3+ file restructuring for one cosmetic startup log; impact is benign.

## Known issues and explored dead ends

**Phase 2D backlog (filed as GitHub issues):**
- #14 — Haiku departure-time hallucination (resolved in Phase 2C.2 prompt fix; left open for tracking)
- #15 — Llama eval bounded by Groq TPD; workarounds documented, not yet implemented
- #20 — Judge cache poisoned by failed-parse score=1 entries; recurs if evals are interrupted mid-run — **deferred to iteration 3** (Part B)
- #21 — Cross-profile coherence requires consistent judge model (current evals mix Qwen3-32B and Sonnet) — **deferred to iteration 3** (Part B)
- #29 — Groq schema enum case sensitivity differs between models
- ~~#45 — Staleness guardrail — closed 2026-05-31 (Phase 2D iteration 5). Implemented as `.github/workflows/production-staleness-check.yml`: daily cron + workflow_dispatch, checks both Cloud Run (via WIF + Artifact Registry SHA tag) and Vercel (REST API + VERCEL_TOKEN secret) drift vs main, opens/updates/closes a single stable GitHub issue (`production-staleness-alert` label). Alert-only — never triggers a deploy. See ADR-0025.~~
- ~~#30 — `[skip ci]` in squash-merge silently suppresses deploys — Issue #30 closed 2026-05-30 — check-no-skip-ci required status check added; [skip ci] commits blocked from merging to main.~~
- ~~#31 — Cache backend selection silent — Issue #31 closed 2026-05-30 — placeholder UPSTASH_REDIS_URL secret replaced and verified via end-to-end Redis read/write test.~~
- ~~#37 — Production secret audit — Issue #37 closed 2026-05-30 — root cause: production frozen at v0.5.0 (pre-Phase-2B image); env bindings applied manually; prod deploy deferred to dedicated iteration.~~

**Dead ends already explored:**
- **NIM Qwen3.5-397B as 4th profile.** Failed at 14/24 completion due to NIM's 1000-credit lifetime pool. Documented and abandoned. Don't retry the same model on NIM unless NIM changes their tier model.
- **Increasing max_tokens for GPT-OSS-120B.** Made truncation *worse* (model used headroom for more hidden reasoning). The fix is `reasoning_effort: low`, not bigger budgets.
- **Per-second RPM throttle for NIM.** Built and tested; doesn't help because the underlying constraint is credit pool, not rate limit. Code remained for defense in depth; don't expect it to fix NIM completion issues.
- **Qwen3-32B as runtime profile.** Same model is used as eval judge; same-family bias would invalidate eval scores. Excluded from demo profile set deliberately.

**pip-audit workflow noise.** ~~Issue #18 closed 2026-05-30 — pip-audit workflow gated on Python file changes; eliminates 0s false failures on non-Python commits. Paths filter now covers `**/*.py`, `**/requirements*.txt`, `**/pyproject.toml`, `**/uv.lock`.~~

## Tests / lint / types — current state

**As of PR #36 merge (commit 41e8e65) — Phase 2C.4.5 complete:**
- 458 tests passing, 85.97% coverage
- ruff check passing
- mypy passing (fixed pre-existing redis_cache.py type errors surfaced by stubs drift)
- Frontend: lint clean, typecheck clean

**Known-broken and accepted:**
- ~~pip-audit workflow's 0s failures (Issue #18) — closed 2026-05-30~~
- pre-existing `find_dotenv()` inconsistency in eval scripts — Issue #49 (migrated from apps/api/docs/backlog.md, Phase 2D iteration 5)

## Open questions I'm flagging honestly

**I don't know:**
- ~~Whether the `[skip ci]` footgun has been fully fixed — resolved: Issue #30 closed 2026-05-30, check-no-skip-ci required status check is active on main.~~
- ~~The exact state of `apps/api/docs/backlog.md` — it was created mid-session and may contain items not migrated to GitHub issues.~~ Resolved: 1 item (BACK-001, find_dotenv cleanup) migrated to Issue #49; file deleted (Phase 2D iteration 5).
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
│   │   └── docs/                     # design notes
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
