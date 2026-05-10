# ADR-0003: Affiliate Redirect vs. Merchant-of-Record Booking

**Status:** Accepted — 2026-05-09

---

## Context

The system must eventually complete a travel booking after the user confirms a package.
There are two fundamentally different ways to monetize and execute that booking:

**Merchant-of-Record (MoR):** We collect payment from the traveler, pay the airline or
hotel on their behalf, own the PNR/reservation, and are responsible for refunds,
chargebacks, and regulatory compliance in every market we operate in.

**Affiliate redirect:** We present the best package to the user, then redirect them to
the airline, OTA, or hotel's own booking page with our affiliate ID embedded. The partner
handles payment, fulfillment, and customer service. We earn a commission (typically 1–3%
of booking value for flights, 3–6% for hotels) when the booking completes.

The system must also support demonstrations and integration testing without executing
real bookings or requiring real money.

This decision has significant downstream implications for:
- **Revenue timing:** MoR generates instant revenue; affiliate revenue reports with a
  30–60 day lag from networks like CJ Affiliate or Awin.
- **Compliance scope:** MoR requires PCI DSS Level 1 compliance, payment processor
  contracts, fraud prevention systems, BSP/ARC accreditation (for IATA flights), and
  regulatory licenses in each jurisdiction.
- **Engineering scope:** MoR requires a payment processing integration (Stripe, Adyen),
  a reconciliation system, chargeback handling, and a customer service workflow.
- **Liability:** Under MoR, a failed booking is our failure. Under affiliate, we are a
  referrer — the partner bears the booking risk.
- **Sellability:** B2B buyers (Skyscanner, MakeMyTrip) have their own MoR infrastructure.
  What they want from us is the agent layer, not payment processing.

---

## Decision

We implement **dual-mode booking**:

### Mode 1 — Test mode (default for v1 demos and integration tests)

`BookingAgent` calls the Amadeus and Duffel sandbox endpoints. These return realistic,
well-formed responses including fake PNRs/booking references that mirror real booking
responses in schema. The full HITL flow (lock → present → confirm → record in audit log)
executes end-to-end.

Test mode is selected via tenant config:
```python
class TenantBookingConfig(BaseModel):
    mode: Literal["test", "affiliate"]
    amadeus_env: Literal["sandbox", "production"]
    duffel_env: Literal["sandbox", "production"]
```

Test mode enables:
- Full end-to-end demos against the Amadeus/Duffel sandboxes.
- Integration tests in CI without network calls to production.
- Tenant onboarding flows where the prospect walks through the complete UX.

### Mode 2 — Affiliate redirect (production revenue path)

Instead of calling `provider.confirm_booking()`, `BookingAgent` calls
`AffiliateRedirectBuilder` which constructs a deep-link URL to the airline or OTA,
pre-populating as many fields as the partner allows (origin, destination, dates,
traveler count, cabin class). The URL includes the tenant's affiliate ID.

```python
class AffiliateRedirectBuilder:
    def build(
        self,
        package: Package,
        tenant_affiliate_config: AffiliateConfig,
    ) -> AffiliateRedirect:
        # Returns: redirect_url, expires_at, affiliate_id, tracking_id
        ...
```

The UI presents the redirect as a button: "Complete booking on [Airline/OTA] →".
A webhook from the affiliate network (CJ, Awin) later reports the completed booking
and commission, which is recorded in the cost ledger as revenue-side data.

The audit log records the affiliate redirect identically to a test-mode booking:
same `booking_audit` row structure, `action = 'affiliate_redirect'` instead of
`'confirm'`, with the redirect URL and tracking ID in `provider_response`.

### The HITL contract is the same in both modes

```
1. OptimizerAgent surfaces two packages.
2. User picks one.
3. BookingAgent calls provider.lock_offer() [test mode] or skips lock [affiliate mode].
4. UI shows the package summary and either:
   - Test: "Confirm booking? Total ₹62,400. Offer holds for 14:32."
   - Affiliate: "Proceed to booking? Total ₹62,400 (estimated). Book on [Partner] →"
5. User explicitly confirms.
6. BookingAgent executes (test: confirm_booking; affiliate: build_redirect).
7. Audit log entry written.
8. Confirmation surfaced to user with PNR [test] or redirect URL [affiliate].
```

The `BookingAgent` implementation uses strategy dispatch on `tenant.booking_config.mode`.
No other agent is aware of the mode distinction.

---

## Consequences

**Positive:**
- Zero PCI DSS scope in v1. No cardholder data ever flows through our system, which
  eliminates the most expensive compliance obligation for a new product.
- Revenue is available from day 1 via affiliate networks, without the 3–6 month lead
  time to establish BSP/ARC accreditation or payment processor contracts.
- B2B buyers (the actual sales targets) already have MoR infrastructure. They are buying
  the agent reasoning layer, not a payment processor. The affiliate model is architecturally
  honest about this.
- Test-mode sandboxes let prospects see the complete booking UX including the HITL flow,
  the audit log, and the booking confirmation — all without real money.
- The `BookingAgent` strategy dispatch means we can add a real MoR mode in Phase 2 as a
  third case in the mode enum, with no changes to `OptimizerAgent`, `CoordinatorAgent`,
  or the audit log schema.

**Negative:**
- Affiliate commission reporting has a 30–60 day lag. We cannot show tenants real-time
  completed-booking revenue in v1 — only redirects initiated. This is acceptable for v1
  but must be addressed for the pricing model dashboard (Phase 11).
- Deep-link pre-population is inconsistent across partners. Some airlines accept full
  pre-population; others accept only origin/destination. The traveler may need to re-enter
  details, adding friction. The `AffiliateRedirectBuilder` must be partner-specific.
- Affiliate network acceptance is not guaranteed. CJ Affiliate and Awin gate on traffic
  volume. We launch with one accepted network and expand. (This risk is noted in
  plan.md §18 and tracked in `docs/backlog.md`.)
- Conversion tracking is partner-dependent. Unlike MoR where we own the booking record,
  affiliate conversion data is whatever the network reports, which may be delayed or
  imprecise.

**Neutral:**
- The `lock_offer()` call is skipped in affiliate mode because there is no offer to lock
  — the partner handles that on their side. The 10–15 minute offer hold shown in the HITL
  flow is replaced with an "estimated price" disclaimer. This changes the UX copy but not
  the state machine structure.
- The audit log entry for an affiliate redirect does not have a `total_cost_minor` value
  confirmed by us — it has the `OptimizerAgent`'s estimated value. The actual charged
  amount is reported by the affiliate network post-booking.

---

## Alternatives Considered

### Alternative 1: Pure affiliate only (no test mode)

No sandbox integration. All demos show mock data; there is no live booking flow.

**Rejected because:**
- Enterprise buyers need to see the HITL flow, the offer lock timer, the audit log, and
  the confirmation UX — not a mock. A "full demo against a real sandbox" is a meaningful
  sales asset.
- Integration tests would need to mock the entire booking flow, increasing the risk of
  test/production divergence.

### Alternative 2: Merchant-of-Record in v1

Build PCI-compliant payment processing, obtain BSP/ARC accreditation, integrate Stripe
or Adyen, implement chargebacks and fraud prevention.

**Rejected because:**
- Timeline: BSP/ARC accreditation alone takes 3–6 months. This is incompatible with a
  12-week v1 delivery.
- Cost: PCI DSS Level 1 compliance costs $50K–$150K/year in audits and tooling.
- Our B2B target buyers already have this infrastructure. Duplicating it to sell to them
  is redundant, not differentiated.
- Revenue risk: if our first MoR booking fails (chargeback, failed payment), we bear the
  financial loss directly. Affiliate mode eliminates this risk entirely for v1.
- Engineering capacity is better spent on the agent layer, which is the actual product,
  than on payment infrastructure.

### Alternative 3: White-label OTA partnership

Partner with an existing OTA (e.g., Travelfusion, Kiwi.com) who acts as MoR. We send
booking requests to them via their API; they handle payment and fulfillment.

**Rejected because:**
- Creates a dependency on a third-party MoR who is also a potential competitor.
- The OTA partnership terms may restrict which tenants we can serve or which markets
  we can operate in.
- Still requires a booking API contract with the OTA, adding a 4th integration
  complexity on top of Amadeus and Duffel.
- The affiliate model achieves the same economic result (commission revenue) with fewer
  dependencies and more flexibility.

---

*Referenced plan.md sections: §2, §7.1, §7.2, §7.3, §7.4, §17*

---

## Amendment 2026-05-11 — Drop test-mode booking

Original Decision specified dual-mode operation: test-mode booking via Amadeus/Duffel
sandbox endpoints for demos, plus affiliate redirect for production revenue. With Amadeus
and Duffel both removed from the provider stack (see ADR-0013), test-mode booking is no
longer available. The Decision simplifies to: affiliate redirect only.

Demos use the same affiliate redirect path that production uses. The "test mode vs
production" distinction collapses into "affiliate marker for demo tenant vs affiliate marker
for paying tenant." Functionally identical, simpler architecturally.

`BookingAgent`'s `lock_offer` / `cancel_offer` methods become no-ops in the affiliate
model — the agent surfaces the booking package, user clicks Book, the redirect happens, the
agent records the click in the audit log. No real lock or cancel exists because we are not
the merchant of record.
