# Project Spec: DealHunter — Wave 1 (Stabilize the Base)

## Strategic context
Demo is done. Goal now: (1) production-grade / usable by real customers, and (3)
push applied-AI sophistication. Agreed 3-wave sequence:
- **Wave 1 (THIS spec):** stabilize the base — reliability, observability, honest
  inventory story.
- Wave 2: eval harness (AI measurement layer — prerequisite for all AI work).
- Wave 3: AI capability/reasoning depth, measured against Wave 2.

Wave 1 picks (GG's stated top weaknesses): the multi-instance booking bug, the thin
synthetic data, and real Aviasales as the primary search story. Observability is
added because you cannot fix reliability you cannot see.

## Goal
A platform stable and observable enough to put in front of a real customer, with an
honest two-sided inventory story: REAL search (Aviasales) + a genuinely convincing
SANDBOX booking flow (upgraded synthetic). No fake "real bookings."

## Current state
- Live: backend Cloud Run rev 00038-sus (Supabase, dealhunter schema, dealhunter_app
  role), frontend Vercel. Planner on Groq (demo-llama). Multi-tenant.
- Aviasales: live SEARCH + affiliate redirect (AVIASALES_LIVE=true). Metasearch only
  — NOT bookable in-app (no PNR/payment).
- DemoProvider: generates deterministic offers for any route; sandbox booking with
  stateless price-change confirm. Thin/synthetic (md5-seeded, limited realism).
- Known intermittent bug: "Booking error — Stream ended unexpectedly" on some
  bookings (others succeed). Suspected multi-instance state / SSE closing on
  unhandled exception.
- Observability: Sentry + Langfuse only half-wired (SENTRY_DSN unset; audit_id null).

### Load-bearing — do NOT change without escalating
Tenancy/RLS/resolver/startup-guard, llm_routing.yaml, optimizer prompt/schema,
booking SSE event contract (unless fixing the bug requires a contract addition —
escalate that).

## Scope — three workstreams, in priority order

### WS1 — Booking reliability (P0)
Fix the intermittent "Stream ended unexpectedly" booking failure so booking
succeeds on EVERY attempt across multiple Cloud Run instances.
- Reproduce against prod; capture the failing path (revalidate/book/cancel), the
  exception, and which instance/revision from Cloud Run logs. Diagnose before fixing.
- Likely class: generated-offer reconstruction or cancel path not fully stateless
  across instances, OR SSE stream closing early on an unhandled exception (no
  error event emitted -> frontend sees "stream ended").
- Fix: ensure every booking sub-path (revalidate, book, confirm, cancel) is
  stateless / instance-independent (reconstruct from offer_id + request, no shared
  in-memory state). Ensure unhandled exceptions in the SSE generator emit a proper
  error event and close cleanly (never a bare stream end).
- VERIFY: smoke 10x in a row on a GENERATED route (search -> book -> price-change
  -> confirm -> PNR -> cancel) AND 10x clean-book. Must be 10/10 (and 10/10 across
  instances if reproducible). Report all results — not "fixed on one run."

### WS2 — Observability (P0, enables everything)
Make prod failures visible.
- Wire Sentry properly (set SENTRY_DSN secret; confirm init_sentry active;
  exceptions + the booking error reach Sentry; populate audit_id so bookings have a
  correlation handle). GG provides/approves the DSN (free tier fine — do NOT block
  on a paid plan).
- Confirm Langfuse traces the agent pipeline (planner/optimizer/refine) in prod;
  if half-wired, finish it.
- Add a minimal structured-logging pass on the booking + search paths so the
  failing path is greppable.
- Add a lightweight uptime/health signal (even a simple external ping on /health).
- Acceptance: when a booking fails, GG can see WHY in Sentry/logs within minutes.

### WS3 — Honest two-sided inventory (P1)
Resolve the real-vs-synthetic question by making BOTH halves genuinely good and
clearly framed — not competing.
- REAL search (Aviasales) as the primary real-data story: ensure an Aviasales-backed
  tenant/path returns real fares with the affiliate redirect working and attributed
  (marker intact). This is the "agent over live inventory" showcase.
- UPGRADED synthetic for the bookable flow: make DemoProvider materially more
  realistic — plausible real airlines/schedules per route, sensible fare spreads by
  distance/cabin, realistic availability, believable times. Goal: the sandbox
  booking demo no longer looks obviously fake. Still clearly labeled sandbox /
  no-payment.
- Framing: the two are the two halves (Aviasales = real search/affiliate; sandbox =
  full booking orchestration for when a platform plugs in bookable inventory). Keep
  per-tenant inventory_adapter as the switch. Do NOT present synthetic as real.
- Propose the approach for "upgraded synthetic" BEFORE building (data source for
  realistic schedules/airlines — static curated tables vs generated; GG approves).

### Out of scope (later waves)
- Eval harness (Wave 2). New AI capabilities / reasoning depth (Wave 3). Real
  bookable inventory (business-gated: GDS/IATA/payments/regulatory). Payments. UI
  redesign.

## Verification
```yaml
- name: tests
  cmd: "pytest -q && (cd apps/web && npm run build && npm run lint && tsc --noEmit)"
  required: true
- name: booking_10x
  cmd: "live prod: 10x book->price-change->confirm->cancel on a generated route, 10/10"
  required: true
- name: observability
  cmd: "trigger a failure; confirm it appears in Sentry + logs with audit_id"
  required: true
- name: real_search
  cmd: "Aviasales path returns real fares + working attributed affiliate redirect"
  required: true
```

## Escalation rules
- WS1: diagnose + show root cause before the fix if it touches the SSE contract.
- WS2: GG provides/approves the Sentry DSN; do not set ANTHROPIC_API_KEY ever; do
  not block on paid observability tiers.
- WS3: show the "upgraded synthetic" data approach before building.
- Any prod deploy: backend canary->full + frontend Vercel, GG-gated (GG approves
  env gate + smokes). PRs reviewed, GG merges.
- Do not touch load-bearing files without escalating.

## Hard rules
- No ANTHROPIC_API_KEY (Claude Max). Free tiers only; no paid infra without GG ok.
- Sandbox bookings stay sandbox:true; never present synthetic as real bookings.
- Deployed code == committed main (the working-tree-vs-deploy lesson); never promote
  on green unit tests alone — verify observed-live.
- Existing tenancy/RLS/isolation tests stay green.

## Success criteria
- Booking succeeds 10/10 across instances (WS1).
- A prod failure is diagnosable from Sentry/logs within minutes; audit_id populated
  (WS2).
- Real Aviasales search returns real fares with working attributed redirect; upgraded
  synthetic looks credible for the bookable demo, clearly labeled sandbox (WS3).
- All tests green; verified observed-live; nothing presented as real that isn't.

## Build order
1. WS1 diagnosis (reproduce + root-cause the booking bug) — report before fixing.
2. WS2 observability (Sentry/Langfuse/logging) — so WS1's fix is verifiable and
   future failures visible. (Can run alongside WS1 diagnosis.)
3. WS1 fix + 10x verification.
4. WS3 real-Aviasales confirmation + upgraded-synthetic (approach approved first).
5. Deploy (canary->full + Vercel), GG-gated; GG smokes observed-live.
```
