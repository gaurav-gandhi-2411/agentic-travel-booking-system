# Project Spec: DealHunter — Phase 3.1 (Live Inventory Activation)

## Goal

Switch the flight pipeline from synthetic data to **live Aviasales inventory**,
without touching any other layer. The `AviasalesAdapter` and the dual-mode
`FlightHunterAgent` constructor already exist; this iteration wires real data in
behind a feature flag, makes the affiliate-deeplink emission toggleable, cleans
accumulated repo hygiene debt, and reconciles the issue backlog.

This is a demo-credibility and optional-affiliate-revenue win. It is **not** the
B2B productization phase (auth, multi-tenancy, BYO real inventory for inventory
owners, booking/payments) — that is Phase 3.2 and beyond, and is explicitly out
of scope here.

## Current state (existing project)

- Production is current and healthy: backend Cloud Run `00019-liy` (main HEAD,
  modulo one docs-only commit `8f11926`), frontend Vercel on `1cf0a07`.
- Quality baseline: 485 tests pass (3 skipped), 86.46% coverage, ruff + mypy clean.
- **Seam already exists:** `AviasalesAdapter` is complete; `FlightHunterAgent`
  has a dual-mode constructor — inject the adapter to activate live data.
  Missing only: `AVIASALES_API_KEY` in prod env + injection at startup.
- `providers/aviasales/deeplink.py` is complete — builds Travelpayouts affiliate
  URLs carried on every archetype card. Currently always emitted.
- Synthetic path (`SyntheticProvider`) is the default and must remain the
  fallback when live mode is off.

### Load-bearing — do NOT touch without escalating (from CURRENT_STATE.md)
- `config/llm_routing.yaml`
- `apps/api/src/travel_agent/agents/optimizer.py` (system prompt — re-baselining required)
- `apps/api/src/travel_agent/evals/optimizer/thresholds.py` and `runner.py`
- `apps/api/src/travel_agent/llm/` adapters
- `apps/api/src/travel_agent/agents/conversation_manager.py` + prompt/types
- `.github/workflows/deploy-*.yml`

### Out of scope this iteration (do NOT build)
- `agents/booking.py` (BookingAgent — stays a stub; Phase E)
- Any payment / payment-gateway code
- Real auth or multi-tenancy (`tenancy/` stays empty; `tenant_id`/`user_id` stay unpopulated)
- Live hotel adapter (hotels stay on `SyntheticProvider`)
- Generalizing the `InventoryProvider` interface (deferred to Phase 3.2 when a 2nd real adapter exists)

## Tech stack
- Python 3.12, FastAPI (existing — no new framework)
- Aviasales / Travelpayouts API via the existing `AviasalesAdapter` (no new SDK unless the adapter already declares one)

## Architecture (no new top-level dirs)
```
apps/api/src/travel_agent/
├── agents/flight_hunter.py        # dual-mode constructor — wire injection here
├── providers/aviasales/
│   ├── adapter.py                 # AviasalesAdapter (read; confirm contract)
│   └── deeplink.py                # affiliate URL builder — gate emission on flag
├── api/                           # startup wiring — inject adapter when AVIASALES_LIVE=true
└── coordinator/                   # unchanged
```

## Feature flags (new, env-driven)
- `AVIASALES_LIVE` (default `false`): when true AND `AVIASALES_API_KEY` is present,
  inject `AviasalesAdapter` into `FlightHunterAgent`; otherwise fall back to synthetic.
- `AFFILIATE_DEEPLINKS` (default `true`): when false, archetype cards carry no
  affiliate deeplink. (Forward-protection for white-label/inventory-owner buyers.)

## Verification commands
```yaml
- name: tests
  cmd: pytest -q
  required: true
- name: lint
  cmd: ruff check .
  required: true
- name: types
  cmd: mypy .
  required: true
- name: live_smoke
  cmd: "manual — real route search against staging after AVIASALES_API_KEY bound"
  required: true
```

## Subagent usage rules
- `executor` for any file write/edit.
- `verifier` for tests/lint/types.
- Orchestrator does NOT write code.
- Repo-hygiene triage (step 1) and backlog reconciliation are cheap `git`/`gh`
  reads/commits — orchestrator may do directly without a subagent.

## Escalation rules (orchestrator must ask before doing)
- Ask before any **production** deploy (canary or full). GG gates prod.
- Ask if the `AviasalesAdapter` contract differs from assumed (e.g. response
  shape, auth header, rate limits, a `raw_link` field the deeplink builder needs) —
  report the real contract, don't guess.
- Ask before deleting or moving the undocumented root `tests/` directory —
  confirm it holds nothing load-bearing first.
- Ask before touching any file in the "Load-bearing" list.
- Stop and escalate if activating live data breaks the synthetic fallback path.
- Confirm `AVIASALES_API_KEY` is actually in GG's hands before the
  bind-and-smoke step; GG obtains it from the Travelpayouts dashboard.

## Hard rules
- Do NOT introduce any booking, PNR, payment, auth, or multi-tenancy logic.
- Do NOT relax eval thresholds to make anything pass.
- Do NOT set `ANTHROPIC_API_KEY` anywhere (GG is on Max — double-bills).
  `AVIASALES_API_KEY` is a separate third-party key and is fine.
- Keep `SyntheticProvider` as the working default when `AVIASALES_LIVE=false`.
- Run the full existing test suite after every executor pass; escalate if any
  previously-passing test fails.

## Budget
- Soft target: 1 CC session.
- Hard cap: stop and escalate after 15 executor invocations.
- Orchestrator runs `/cost` at midpoint and reports.

## Success criteria (verify ALL before declaring done)
- With `AVIASALES_LIVE=true` + key bound, a real-route search in **staging**
  returns real fares (confirmed via logs/response, not synthetic constants).
- With `AVIASALES_LIVE=false`, the synthetic path works unchanged.
- `AFFILIATE_DEEPLINKS=false` suppresses affiliate URLs on archetype cards;
  `true` restores them. Both verified by test.
- New injection + flag logic has unit tests; synthetic-fallback test added.
- 485+ tests pass, coverage ≥ 86%, ruff + mypy clean.
- Repo hygiene resolved: untracked eval reports/runs triaged (commit or
  gitignore), `uv.lock` committed, root `tests/` dir resolved.
- Backlog reconciled: close #6 (ConversationManager — implemented) and #9
  (demo-qwen replaced — done); note #56 is the known #54 docs-only-drift pattern.
- No change to booking/payment/auth surface.

## Build order
1. **Hygiene + backlog.** Triage 20 untracked files (commit eval artifacts or
   add to `.gitignore`), commit `uv.lock`, resolve root `tests/` dir (escalate
   if unsure), close #6 and #9, comment-and-note #56.
2. **Read the seam.** Inspect `AviasalesAdapter`, `FlightHunterAgent` dual-mode
   constructor, and `deeplink.py`. Report the real adapter contract before wiring.
3. **Wire injection** behind `AVIASALES_LIVE` (default false); affiliate emission
   behind `AFFILIATE_DEEPLINKS` (default true). Synthetic remains fallback.
4. **Tests.** Injection, both flags, synthetic-fallback. Verifier pass.
5. **Staging deploy + live smoke** on a real route (after GG confirms key bound).
6. **Prod** canary → GG smoke → full. Update CURRENT_STATE.md. (GG-gated.)
