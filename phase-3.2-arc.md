# DealHunter — Phase 3.2 Arc (B2B Productization Roadmap)

**Purpose:** turn DealHunter from "impressive live-data demo" into "an agentic
search/recommendation layer an inventory owner (IndiGo, Air India, Booking.com,
an OTA) can license over *their* inventory." This is the Persona-B product.

**Planning doc, not a CC spec.** Each iteration below is independently shippable.
Pick one; I'll write its `spec.md` + orchestrator kickoff. Recommended default
order at the end.

---

## The commercial thesis (what we're selling)

A licensee already owns inventory and a booking backend. They do NOT want our
synthetic data or our Aviasales affiliate redirect. They want our pipeline —
Planner → FlightHunter → Optimizer → ConversationManager, plus the eval rigor
and cheap-LLM cost story — running over an adapter that speaks to *their*
inventory, isolated per tenant, metered, behind a contract their engineers can
build against. Everything in 3.2 serves that.

Two facts already in our favor (from recon): `tenant_id`/`user_id` fields exist
in `RequestState` (unpopulated), and the affiliate deeplink is already behind a
per-deploy flag (`AFFILIATE_DEEPLINKS`) so it can be OFF for a white-label buyer.

---

## Iteration A — Tenancy foundation (auth + isolation)

**Goal:** replace the single shared `DEMO_API_KEY` with real per-tenant identity,
and make tenant context flow through the pipeline.

**Scope:**
- Per-tenant API keys: issue / store / validate. Replace `DemoAuthMiddleware`
  (`api/middleware/auth.py`) with tenant-key resolution.
- Populate `tenant_id` / `user_id` into `RequestState` from the authenticated key.
- Fill the empty `tenancy/` module: `Tenant` model, key→tenant resolution,
  per-tenant config (which inventory adapter, affiliate on/off, labels/branding,
  rate-limit tier).
- **Architecture fork to decide here:** durable tenant store. Today only Upstash
  Redis (cache) exists. Tenancy needs persistence — Postgres is the production
  path, and ADR-0003/0004 already reference Postgres + row-level security. RLS
  is the clean tenant-isolation mechanism.

**Key seams:** `api/middleware/auth.py`, `coordinator/state.py` (RequestState),
`tenancy/__init__.py` (empty), per-tenant config consumed in `_get_adapter()`.

**Unlocks:** everything else. This is the spine of B2B.
**Depends on:** nothing. Do first.
**Business gate:** none — fully codeable now.

---

## Iteration B — Metering, quotas, rate-limiting

**Goal:** per-tenant usage accounting and enforcement — the thing that makes it
sellable as metered SaaS.

**Scope:**
- Per-tenant request + token metering (searches, refines, LLM tokens).
- Per-tenant quotas + rate limits enforced in FastAPI middleware. (Today's
  `ThrottledLLMClient` only protects *our* Groq budget — not per-tenant.)
- Usage records suitable for later billing (billing itself is out of scope).

**Unlocks:** the SaaS pricing story; protects shared LLM budget from one noisy
tenant. Directly answers a prospect's "how do you meter and isolate us?"
**Depends on:** A (needs tenant identity).
**Business gate:** none — codeable now. (Actual invoicing/payment = later.)

---

## Iteration C — Second inventory adapter + interface generalization

**Goal:** prove the BYO-inventory story by running a *second* real adapter behind
the same seam, and generalize the `InventoryProvider` interface off two real
implementations instead of one.

**Scope:**
- Add a second adapter — ideally a **bookable** source (an airline-direct NDC
  sandbox, or a TBO/aggregator sandbox), not another metasearch. A bookable
  adapter is what proves the pitch to an inventory owner; Aviasales alone is
  metasearch-shaped.
- Generalize `InventoryProvider` from the two impls so a licensee's own inventory
  is a drop-in (return bookable fares, not just deeplinks).
- Per-tenant adapter selection (wires to A's tenant config).

**Unlocks:** the core demo for IndiGo/Booking.com — "here's your inventory in our
pipeline." De-risks the interface against real-world variety.
**Depends on:** A for per-tenant selection; the pure-adapter build can start in
parallel with A.
**Business gate:** a sandbox/test API for the chosen source (most NDC/aggregator
sandboxes are free to register — that's a GG signup, not a contract).

---

## Iteration D — Licensee-facing API contract + OpenAPI + docs

**Goal:** the sellable artifact — a stable, versioned, documented API a prospect's
engineering team can read and integrate against.

**Scope:**
- Stable versioned routes (`/v1/...`), generated + published OpenAPI spec.
- Documented SSE event contract, auth/onboarding guide (how a licensee gets keys
  and plugs in their inventory adapter), error contract.
- A short integration quickstart.

**Unlocks:** moves you from "demo I show" to "product they evaluate." This is what
goes in front of a buyer's tech team.
**Depends on:** A/B/C stable enough to freeze a contract (freeze last).
**Business gate:** none.

---

## Iteration E — Booking / payment scaffolding (toward full OTA — BUSINESS-GATED)

**Goal:** implement the transaction layer against sandboxes. This is the boundary
into target 3 (full OTA).

**Scope (sandbox-only):**
- Implement `BookingAgent` (`agents/booking.py`, currently a `NotImplementedError`
  stub) against a GDS/NDC sandbox. The `BookingStatus` schema already exists
  (`coordinator/state.py:171` — pnr, offer_lock_id, hold_expires_at,
  idempotency_key, audit_id).
- `PaymentGateway` interface against a Razorpay/Cashfree **test** environment.
- Write the `audit_id` trail; add PII handling / log scrubbing (LLM traces
  currently flow to Langfuse with no masking); DPDP-aware data handling.

**Unlocks:** the full-OTA path — actually transacting bookings.
**Depends on:** A (tenant identity), and realistically D (contract).
**⚠ BUSINESS TRACK — on GG, not CC; blocks GOING LIVE (not the sandbox code):**
- GDS/NDC commercial contract or a consolidator partnership
- IATA accreditation or a ticketing partner
- Live payment gateway + PCI-DSS scope
- DPDP Act 2023 compliance, GST e-invoicing, any TCS/RBI obligations on travel
  (verify current rules with counsel before going live)
None of that is code; most is months and signatures. CC can build and test the
scaffolding against sandboxes now, but real money / real tickets stay off until
these clear.

---

## Cross-cutting (fold into iterations, not separate passes)

- **Do early (cheap, aids everything):** Sentry (#10) for error aggregation,
  branch protection (#12). Worth having before multi-tenant traffic.
- **Do when volume matters:** prompt caching (#33/#34/#35) — a cost optimization
  that pays off once tenant request volume is real.
- **Do alongside D:** promote the optimizer eval to a blocking CI gate (#8) —
  contract stability deserves a quality gate.

---

## Recommended default sequence

1. **A** (tenancy foundation) — spine; everything depends on it. Fold in Sentry +
   branch protection here.
2. **C** (second adapter) — start the pure-adapter part in parallel with A; finish
   the per-tenant selection after A lands. This is the buyer-facing proof.
3. **B** (metering/quotas) — once tenants exist, meter them.
4. **D** (contract + OpenAPI) — freeze and document once A/B/C are stable. Fold in #8.
5. **E** (booking/payment) — scaffold against sandboxes when you want it; going
   live waits on the business-track gates above.

A → C → B → D gets you a **licensable agentic layer** — the sellable product for
your named buyers — without touching the regulated booking/payment surface. E is
the bridge to full OTA when the business side is ready.

---

**Next step:** tell me which iteration to spec first (default: A). I'll write its
`spec.md` + orchestrator kickoff in the usual format.
