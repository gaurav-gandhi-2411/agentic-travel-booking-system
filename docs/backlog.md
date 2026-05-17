# Backlog

Items deferred from current phase scope. Each entry notes the originating phase and reason.

---

## Phase 2C follow-ups

- **Use `find_dotenv()` for all .env loading in eval and dev scripts** _(flagged 2026-05-17, Phase 2C.1 sanity run)_
  Several scripts use relative paths like `../.env` that break silently depending on CWD.
  `find_dotenv()` walks up the tree and is robust. Audit: `judge.py`, `runner.py`, `scorer.py`,
  `evals/run.py`, any ad-hoc dev scripts calling `load_dotenv()` with a hardcoded path.
  Small standalone cleanup PR, no functional change.

- **Add `all_samples: list[dict]` to JudgeScore for variance root-cause analysis** _(flagged 2026-05-17, Phase 2C.1 diagnosis)_
  `all_scores: list[int]` is already exposed and sufficient for flagging high-variance scenarios.
  For deeper debugging (why did sample 1 score differently?), add `all_samples: list[dict]`
  storing each sample's raw text, parsed score, and parsed reason. Not a blocker for baseline;
  only useful when investigating `high_variance=True` scenarios post-baseline.

- **Narrow exception handling in `CoherenceJudge.score()`** _(flagged 2026-05-17, Phase 2C.1 code review)_
  `except (json.JSONDecodeError, Exception)` — the broad `Exception` swallows everything.
  Replace with explicit types: `json.JSONDecodeError`, `KeyError`, `ValueError`,
  `httpx.HTTPError`, `asyncio.TimeoutError`. Same discipline as PR #4 Redis graceful degradation.

- **Add tiebreak comment in `CoherenceJudge.score()` best-sample selection** _(flagged 2026-05-17)_
  `min(parsed_samples, key=lambda p: abs(...))` picks the first sample in iteration order when
  multiple samples tie on distance to median. Add one-line comment `# tiebreak: first sample wins`
  so future readers don't second-guess it.

- **Haiku hallucinates departure times not in FlightOption schema** _(flagged 2026-05-17, baseline opt-003 + opt-019; tag: phase-2c-followup, prompt-tuning)_
  Two baseline high-variance archetypes revealed Haiku citing departure times (e.g., "10:30 AM",
  "9:30 AM") that do not exist in the `FlightOption` schema. When the LLM judge detects the
  unverifiable time claim it scores structural_valid=False (score 2); when it focuses on the
  verifiable attributes (stops, duration) it scores 5. This is the root cause of both HV cases.
  Possible fixes (Phase 2C.2 to decide):
  (a) Add `departure_at` to `FlightOption` and pass it to the optimizer prompt.
  (b) Constrain optimizer system prompt: "Only mention attributes explicitly present in the
      flight data. Do not infer or invent timing claims."
  (c) Post-process explanation output to strip ungrounded timing phrases (regex, conservative).
  Reference: baseline reports opt-003 and opt-019, all_scores=[5,4,2] and [5,2,5].

- **Optimizer eval runner hits Groq token quota on Llama before completing 24 scenarios** _(flagged 2026-05-17, baseline opt-007/023/024; tag: phase-2c-followup, runner-resilience)_
  Baseline 2026-05-17: the Llama 24-scenario run triggered TPM (opt-007) then TPD (opt-023,
  opt-024) 429 errors, leaving 3 scenarios without archetypes. No pacing or quota-aware logic
  exists in `runner.py`. Possible fixes:
  (a) Add inter-scenario sleep to stay within TPM (currently fires all scenarios sequentially
      but without back-off).
  (b) Multi-provider fallback: on Groq 429, re-route the affected scenario through OpenRouter
      or another free tier.
  (c) NVIDIA NIM as a fallback target (Phase 2C.2 option from external engineer).
  (d) Pre-flight quota check: estimate total tokens before starting; refuse if insufficient.
  Reference: 20260516T232614_demo-llama run; opt-007 (TPM), opt-023/024 (TPD).

- **Add pre-commit hook for ruff check + ruff format** _(flagged 2026-05-17)_
  Three consecutive PRs (#1, #2) have required cleanup commits for lint debt left by earlier
  commits that bypassed CI. Add `.pre-commit-config.yaml` running `ruff check` and
  `ruff format --check` as a local pre-commit hook. Stops the cycle at the source.
  Setup: `pip install pre-commit && pre-commit install`. Config:
  ```yaml
  repos:
    - repo: https://github.com/astral-sh/ruff-pre-commit
      rev: v0.11.9  # pin to current project ruff version
      hooks:
        - id: ruff
        - id: ruff-format
  ```
  Cost: ~5 minutes of setup, zero ongoing overhead.
  File to create: `.pre-commit-config.yaml` at repo root.

- **Redis cache failure should degrade gracefully, not crash /search** _(flagged 2026-05-16)_
  A Redis connection failure (wrong scheme, network blip, Upstash outage) currently raises
  through `search_cache.put()` in `coordinator/streaming.py` and aborts the entire SSE
  stream with a generic error event. The `/health` endpoint already returns `503` in prod
  mode on unreachable Redis (correct); the runtime path should not abort. Wrap
  `search_cache.put()` and `search_cache.get()` calls in `try/except` that log a structured
  warning and continue. Incident reference: `redis://` → `rediss://` misconfiguration on
  2026-05-16 caused full /search failure rather than graceful degradation.
  File: `apps/api/src/travel_agent/coordinator/streaming.py:195`.

- **Move optimizer eval from weekly to nightly cron** _(flagged 2026-05-16)_
  Free-tier model deprecations (OpenRouter dropped `qwen-2.5-72b-instruct:free`, caught
  2026-05-16) need detection within 24h, not a week. Change
  `cron: '0 6 * * 1'` → `cron: '0 6 * * *'` in `.github/workflows/eval-optimizer.yml`.
  Cost: Haiku calls cost ~$0.034/run × 30 days ≈ $1/month — within budget. Note: the
  eval workflow currently uses `--dry-run`, so switching to nightly costs nothing until
  live LLM keys are wired into CI (separate ticket). Start with: nightly dry-run to catch
  config/schema breakage; add live-key nightly once CI budget is approved.

---

## Phase 1 follow-ups

- **Re-add `/health` smoke test to both deploy workflows**
  `deploy-staging.yml` and `deploy-prod.yml` both have a `# TODO(Phase 1)` marker where
  the `curl --fail $SERVICE_URL/health` step was removed during Stage 0.4 provisioning
  (the endpoint didn't exist yet). Once Phase 1 ships the `/health` endpoint that issues
  `SELECT 1`, re-add the step to each workflow so deploy CI verifies the service is
  actually serving before the run turns green. _(Flagged Stage 0.4.)_

---

## Provider milestones

- **Booking.com affiliate program — re-apply after Phase 0.5 ships**
  Previous application rejected due to absence of a credible project URL. Re-apply once
  the Phase 0.5 marketing site is live on Vercel with developer-tier framing. Gates real
  hotel data in `hotel_hunter` (currently running on Synthetic provider per ADR-0013).
  _(Gated on Phase 0.5 Vercel deploy.)_

- **Secondary hotel programs (Agoda, Hotellook, Trip.com, Expedia) — gate on Booking signal**
  Apply sequentially after Booking.com acceptance. Booking's approval response signals
  which tier of affiliate programs will accept at current traffic levels; use that signal
  to prioritize rather than burning application attempts across all programs in parallel.
  _(Gated on Booking.com acceptance.)_

- **Kiwi.com + Amadeus Enterprise — re-evaluate after incorporation**
  Kiwi requires partner application via email and reviews against business registration.
  Amadeus Self-Service decommissions July 2026 but the Enterprise tier opens post-
  incorporation. No committed incorporation date; revisit when that milestone lands.
  _(Gated on incorporation milestone, no committed date.)_

- **v2 provider candidates — niche travel categories**
  WayAway (LCC flights, Travelpayouts program), Klook (activities), Hostelworld
  (hostels), Vrbo (vacation rentals). Not v1 scope; add to Phase 2 planning once
  the core v1 provider stack is stable. _(Flagged ADR-0013.)_

---

## Phase 11

- **Tenant provider account onboarding guide**
  Amadeus and Duffel require per-tenant developer accounts (we never share a credential
  pool — §8.4). Add a step-by-step walkthrough to `docs/runbooks/tenant-onboarding.md`
  and frame in `docs/customer/integration-guide.md` as "you keep your existing provider
  relationships and quotas." _(Flagged Phase 0; Risk 2.)_

- **Agent eval: 50+ golden cases per agent**
  Phase 3 sign-off bar is 10–20 cases/agent. Expanding to 50+ is a stretch target moved
  here. Cases are Claude-generated then human-reviewed; workflow documented in
  `docs/architecture/eval-strategy.md` (written Phase 3). _(Flagged Phase 0; Risk 4.)_

---

## Phase 8

- **ADR (TBD): Frontend auth — Clerk**
  Document rationale for Clerk over NextAuth.js and Supabase Auth (multi-tenant org model,
  generous free tier, native Next.js integration). ADR number assigned in Phase 8 (after
  ADR-0012 from the open-source track). _(Decision made Phase 0.)_

- **ADR (TBD): Vercel AI SDK for streaming**
  Document rationale for Anthropic Python SDK on backend + Vercel AI SDK on Next.js
  frontend for streaming only. ADR number assigned in Phase 8. _(Decision made Phase 0.)_

- **KMS migration when commercial**
  Current encryption uses AES-256-GCM at the application layer (ADR-0007). When the
  project becomes commercial and Secret Manager costs are no longer the binding constraint,
  migrate tenant-credential encryption to Cloud KMS for HSM-backed key management and
  automatic rotation without application-layer re-encryption. _(Flagged ADR-0007.)_

- **vLLM serving when scale demands**
  Current inference uses Ollama (local) or OpenRouter/Groq (free cloud). When request
  volume justifies dedicated GPU infrastructure, replace with vLLM self-hosted serving
  (ADR-0008, VLLMAdapter stub in Phase 2.5). Trigger: sustained >500 req/day or SLA
  requirements that free-tier rate limits cannot meet. _(Flagged ADR-0008.)_

- **Cost ledger end-user visibility flag**
  Implement `show_cost_to_users: true` tenant config flag. Tenant admin dashboard shows
  cost by default; end-user chat UI hides it unless the flag is set. _(Decision made
  Phase 0; §15.)_

---

## Research Track

- **benchmark-protocol.md: enforce full-sample requirement in CI**
  `docs/research/benchmark-protocol.md` states that external reproducers must run the
  complete 20% public eval sample. Add a CI check (Phase 3.5 or 6.7) that asserts
  the benchmark run consumed all examples in `evals/datasets/` — not a subset. This
  prevents accidental subset runs from being reported as reproductions. _(Flagged sub-stage A.)_

- **Technical report §7 Limitations: 80% dataset proprietary**
  When writing the technical report (Phase 6.7), §7 Limitations must explicitly state
  that 80% of the golden eval dataset is proprietary and not publicly released. External
  reproducers work from the 20% public sample only; their results may differ from the
  reported numbers due to sample variation. This is documented in `paper-outline.md` §7
  but must carry through verbatim to the final submitted draft. _(Flagged sub-stage A.)_

## Phase 3

- **Eval strategy document**
  Write `docs/architecture/eval-strategy.md` covering: Claude-generated golden cases,
  human-review workflow, how cases are updated when prompts change. _(Flagged Phase 0.)_

---

## Phase 11.5 (Technical Report)

- **About page: tighten "the agent handles..." sentence**
  `apps/web/app/(marketing)/about/page.tsx` currently reads "the agent handles window
  search, scoring, ranking, explanation, and refinement routing." Those capabilities are
  architected, not all built at time of writing. Surrounding context ("adapter pattern",
  "targets fine-tuned models") disambiguates sufficiently for the marketing site, but the
  wording should be tightened when the technical report is drafted to reflect actual
  shipped vs. designed capabilities accurately. _(Flagged Phase 0.5-D.)_

---

## Resolved / In-scope

- ~~**Neon cold-start mitigation**~~ → Handled in Stage 0.4 cloud-setup runbook via Cloud
  Scheduler cron hitting `/health` every 4 minutes. _(Risk 1.)_
