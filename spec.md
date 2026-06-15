# Project Spec: DealHunter — Phase 3.2-G (Demo Last-Mile Fixes)

## Goal

Fix the demo-presentation-layer bugs found in the live /demo audit so a prospect
can drive the full loop without hitting credibility-breaking or confusing states.
Backend SSE is clean (audit found zero backend bugs); all fixes are frontend +
demo-tenant config. No tenancy/RLS/resolver changes.

**Sandbox demo only. Frontend + config. No backend SSE/agent logic changes.**
Prod backend is live (00032-cex); deploy frontend to Vercel + at most a demo-tenant
config/catalog change to the backend if B1 requires it (gated).

## Current state

- Live: backend 00032-cex (Groq planner), frontend on Vercel /demo, multi-tenant
  on Supabase. Demo tenant uses DemoProvider (inventory_adapter="demo").
- Audit (2026-06-15): backend SSE clean. 5 demo-layer issues: C1, B1, F1, F2, C2.
- Per-tenant `affiliate_enabled` flag EXISTS (from 3.2-A) — it's just set true for
  the demo tenant, which is wrong for a demo.

### Load-bearing — do NOT touch without escalating
- Tenancy/RLS/resolver, llm_routing.yaml, optimizer.py prompt/thresholds, llm/ adapters
- Backend SSE event contract and booking_streaming logic (audit confirmed clean)

## Scope (in priority order)

### P0 — C1: suppress Aviasales affiliate button for the demo tenant
- The demo tenant must be `affiliate_enabled=false` so DemoProvider offers carry
  NO Aviasales deeplink. A prospect must never see a "Book on Aviasales" button
  next to a ₹4,850 demo fare that opens real ₹40k+ Aviasales results.
- Prefer the existing per-tenant `affiliate_enabled` mechanism: set the demo
  tenant's config to affiliate_enabled=false (config/seed change), and confirm the
  frontend hides the Aviasales CTA when an offer has no deeplink_url.
- Frontend: when `deeplink_url` is absent/empty, do NOT render the "Book on
  Aviasales" button at all (no broken/empty button).

### P0 — B1: make the price-change trust moment reachable from the UI
- The price-change flow is DealHunter's signature trust moment and is currently
  unreachable (FLT-005 is never surfaced as an archetype; it's price-dominated by
  FLT-001).
- Fix at the DEMO CATALOG level, NOT by forcing the optimizer: restructure the
  DemoProvider catalog so a SURFACED archetype demonstrates the re-price. Options
  (orchestrator proposes, GG approves):
  (a) Make the price-change trigger an offer the optimizer DOES surface (e.g. the
      best-value or best-experience archetype's underlying offer returns
      price_changed=true on revalidate), or
  (b) Restructure the catalog so FLT-005 is the best-value pick (not dominated),
      so it becomes an archetype and its Book triggers the price change.
- Goal: clicking "Book this flight" on a visible archetype card can reach the
  price-change confirm step. Keep at least one offer that books cleanly with NO
  price change too (so both paths are demoable).
- This may touch DemoProvider (backend) — that's allowed for the demo catalog
  ONLY; do not change provider contracts or the optimizer.

### P1 — F2: disable ALL book buttons during an in-progress/confirmed booking
- Clicking the other archetype card's "Book this flight" while a booking is
  in-flight or confirmed currently aborts and silently destroys the confirmed PNR.
- Fix: while `booking.status !== 'idle'`, disable "Book this flight" on ALL
  archetype cards (not just the selected one), OR require the active booking to be
  closed/cancelled first. No silent destruction of a confirmed booking.

### P1 — F1: add a dismiss/close affordance to the confirmed state
- The confirmed BookingPanel has only "Cancel this booking" — a viewer must cancel
  to exit, so a confirmed booking can't be left on screen.
- Add a "Done"/"Close" (dismiss) action in the confirmed state that closes the
  panel WITHOUT cancelling the booking. Include `confirmed` in the showClose logic.

### P2 — C2: audit_id (low, optional this pass)
- audit_id is null because Sentry DSN is unset. Out of scope unless trivial;
  acceptable to defer. Do NOT set a real SENTRY_DSN as a blocking step.

### Out of scope
- Backend SSE/agent/booking logic changes (audit clean), real inventory, payments,
  tenancy/RLS/resolver, optimizer tuning, landing-page copy.

## Verification
```yaml
- name: web_build
  cmd: "cd apps/web && npm run build && npm run lint && tsc --noEmit"
  required: true
- name: api_tests
  cmd: "pytest -q (if DemoProvider catalog changed)"
  required: true
- name: manual_browser
  cmd: "live /demo: search -> see NO Aviasales button on demo offers; book clean offer -> confirm -> Done (dismiss, not cancel); reach price-change step from a visible card; other book buttons disabled mid-booking"
  required: true
```

## Escalation rules
- Show GG the B1 catalog-restructure approach (a vs b) BEFORE implementing — it
  affects what the demo shows.
- Escalate before any backend change beyond the DemoProvider demo catalog / demo
  tenant config.
- Frontend deploy to Vercel = prod promotion → GG-gated (show what deploys, stop
  for go). Any backend redeploy (if B1 touches DemoProvider) = canary→full, GG-gated.
- Do NOT touch load-bearing files. Do NOT set ANTHROPIC_API_KEY.

## Hard rules
- Sandbox only; sandbox:true preserved. Demo tenant affiliate_enabled=false.
- No backend SSE/agent logic changes; existing tests stay green.
- Frontend changes additive to existing search/refine UI behavior.
- Deploys GG-gated (frontend Vercel; backend canary→full if touched).

## Success criteria
- Demo offers show NO "Book on Aviasales" button (C1 fixed; demo tenant affiliate-off).
- The price-change confirm step is reachable by booking a VISIBLE archetype card
  (B1 fixed); a clean no-price-change booking is also still demoable.
- During an in-progress/confirmed booking, book buttons on all cards are disabled;
  a confirmed PNR cannot be silently destroyed (F2 fixed).
- Confirmed state has a Done/Close that dismisses without cancelling (F1 fixed).
- web build/lint/typecheck clean; backend tests green if DemoProvider touched.
- Verified live in a browser on /demo.

## Build order
1. C1: set demo tenant affiliate_enabled=false (config/seed) + frontend hide
   Aviasales CTA when no deeplink_url. Verify offers show only "Book this flight".
2. B1: propose catalog-restructure (a vs b), show GG, implement so a visible
   archetype reaches the price-change step; keep one clean-booking offer.
3. F2: disable all book buttons while booking.status !== 'idle'.
4. F1: add Done/Close dismiss to confirmed state.
5. Build + lint + typecheck; backend tests if DemoProvider changed.
6. Deploy (frontend Vercel; backend canary→full if touched) — GG-gated; GG browser-smokes /demo.
```
