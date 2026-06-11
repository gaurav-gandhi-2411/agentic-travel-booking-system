# Project Spec: DealHunter — Phase 3.2-E.2 (Booking UI in the Demo)

## Goal

Make the booking loop **clickable**. Surface the full lifecycle — select an offer
from search results → see it re-priced (revalidate) → confirm (book) → see the
confirmation (PNR + hold expiry) → optionally cancel — in the existing Next.js
demo UI, consuming the `/book` and `/cancel` SSE endpoints built in 3.2-E.1.

This is what turns the product from "an API that books" into "a thing a prospect
can drive in a browser." It is a **sandbox** experience end-to-end: the UI must
make clear bookings are simulated (no payment, no real ticket).

**Local/test only — no deploy** (blocked by the Cloud SQL gate regardless).
Prod stays `00025-gaw`.

## Current state (existing project)

- Frontend: Next.js 15 / React 19 on Vercel (`apps/web`). Components under
  `components/demo/` (DemoClient, ProfileToggle, ChatLog, ChatMessage); SSE
  consumed via `hooks/useSearchStream`; `lib/event-map.ts`, `lib/chat-types.ts`.
- The search + refine flow already works in the UI against the live backend.
- Backend booking endpoints exist (3.2-E.1): `POST /book`, `POST /cancel`, SSE
  events `booking_revalidating`, `booking_priced` (carries `price_changed` +
  both prices), `booking_confirmed` (pnr, hold_expires_at), `booking_cancelled`,
  `booking_error` (codes: `not_bookable`, `conflict`, `unavailable`,
  `provider_error`, plus any `not_found` used by cancel). Every event carries
  `sandbox: true`.
- Booking requires a bookable-provider tenant (`mock_bookable`); a search-only
  tenant (Aviasales) is gated out with `booking_error{not_bookable}`.

### Load-bearing — do NOT touch without escalating
- Backend (all of `apps/api`) — this iteration is frontend-only
- The existing search/refine UI flow — preserve its behavior exactly
- `.github/workflows/deploy-*.yml`

## Scope

### In scope
- **Read `/mnt/skills/public/frontend-design/SKILL.md` first** (and any frontend
  conventions in the repo) before writing UI code.
- A **"Book" affordance** on archetype/offer cards in the existing results view.
  Selecting it starts the booking flow for that `offer_id`.
- A **booking flow UI** consuming `/book` SSE: a re-pricing state
  (`booking_revalidating`), a **price-confirmation step** when `price_changed=true`
  (show old vs new price; require an explicit user confirm — never auto-book), a
  confirmed state (`booking_confirmed`: PNR, hold-expiry countdown), and a
  **Cancel** action calling `/cancel`.
- A consumer hook (`hooks/useBookingStream` or equivalent) mirroring
  `useSearchStream`'s pattern for the booking SSE.
- **Sandbox labeling:** the UI clearly marks the booking as simulated (a visible
  "Sandbox / demo booking — no payment taken" badge on the confirmation).
- **Graceful error states:** `not_bookable` (this inventory source can't book),
  `unavailable`, `conflict`, `not_found`, `provider_error` each render a clear,
  non-crashing message.
- An idempotency key generated client-side per booking attempt and reused on
  retry of the *same* offer.

### Out of scope (do NOT build)
- Any backend change (endpoints, events, agents) — frozen this iteration.
- Payments UI, passenger-details forms, seat selection (no real booking data path yet).
- Per-tenant admin/onboarding UI (later).
- Cloud deploy / Vercel deploy / Cloud SQL.

## Tech stack
- Next.js 15 / React 19, existing styling system. No new UI framework. Escalate
  before adding any dependency.

## Verification commands
```yaml
- name: web_build
  cmd: "cd apps/web && npm run build"
  required: true
- name: web_lint
  cmd: "cd apps/web && npm run lint"
  required: true
- name: web_typecheck
  cmd: "cd apps/web && npm run typecheck (or tsc --noEmit)"
  required: true
- name: web_tests
  cmd: "cd apps/web && npm test (if a test runner is configured)"
  required: false
- name: manual_flow
  cmd: "run web locally against a mock_bookable tenant; click search -> book -> confirm -> cancel"
  required: true
```

## Subagent usage rules
- `executor` for code; `verifier` for build/lint/typecheck. Orchestrator does NOT write code.

## Escalation rules (orchestrator must ask before doing)
- At step 1, after reading the frontend skill + existing demo components, propose
  the UI flow (where Book sits, the price-confirmation step, the states) and show
  GG BEFORE building.
- Escalate if surfacing booking requires changing the existing search/refine UI
  behavior — additive only.
- Escalate if the backend SSE contract appears insufficient for a clean UX (e.g. a
  field the UI needs is missing) — report it; do NOT change the backend here.
- Ask before installing any dependency or any deploy.

## Hard rules
- Frontend only. No backend edits.
- Existing search/refine UI behavior preserved exactly.
- Price-changed never auto-books — explicit user confirmation required, mirroring
  the backend gate.
- Sandbox booking is visibly labeled as simulated; never imply a real ticket/payment.
- Do NOT deploy (no Vercel deploy, no backend deploy).

## Budget
- Soft target: 1 CC session. Hard cap: stop and escalate after 18 executor invocations.
- `/cost` at midpoint, reported.

## Success criteria (verify ALL before declaring done)
- From the demo UI against a `mock_bookable` tenant: a user can search, pick an
  offer, see it re-priced, confirm, and see a PNR + hold-expiry confirmation —
  end to end, in the browser.
- Price-changed path shows old vs new price and requires explicit confirm (no auto-book).
- Cancel works from the confirmation view and reflects the cancelled state.
- A search-only (Aviasales) tenant shows a clear "this inventory source can't book"
  state instead of a Book action that fails.
- All `booking_error` codes render clean, non-crashing messages.
- Sandbox/simulated labeling is visible on the booking confirmation.
- Existing search/refine UI flow unchanged.
- `npm run build`, lint, and typecheck pass clean. No deploy.

## Build order
1. Read `frontend-design/SKILL.md` + existing `components/demo/*`, `useSearchStream`,
   `event-map.ts`, `chat-types.ts`. Propose the booking UI flow + states + where
   the Book affordance sits. Show GG; no code yet.
2. `useBookingStream` hook consuming `/book` SSE (mirror `useSearchStream`).
3. Booking flow UI: Book affordance → revalidating → price-confirm (if changed) →
   confirmed (PNR + hold countdown) → cancel. Sandbox badge.
4. Capability + error states (not_bookable for Aviasales tenant; all error codes).
5. Verify: build + lint + typecheck; manual click-through search→book→confirm→cancel
   locally against a mock_bookable tenant.
6. Report. No deploy. Note the product is now demonstrable end-to-end in a browser.
```
