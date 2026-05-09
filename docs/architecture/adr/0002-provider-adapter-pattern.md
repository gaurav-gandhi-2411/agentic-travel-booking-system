# ADR-0002: Provider Adapter Pattern

**Status:** Accepted — 2026-05-09

---

## Context

The system must call multiple travel inventory APIs to retrieve flights and hotels. In v1:
- **Flights:** Amadeus Self-Service API, Duffel API
- **Hotels:** Amadeus Self-Service API

Each provider has a completely different interface:

| | Amadeus | Duffel |
|---|---|---|
| Auth | OAuth2 client credentials (token refresh) | Bearer token (static per environment) |
| Flight search | `GET /v2/shopping/flight-offers` with complex query params | `POST /air/offer_requests` → poll `GET /air/offers` |
| Offer lock | `POST /v1/shopping/flight-offers/pricing` | `POST /air/offer_requests/{id}` then order creation |
| Booking | `POST /v1/booking/flight-orders` | `POST /air/orders` |
| Response schema | Deeply nested JSON with IATA codes, segments, traveler pricing | Flat-ish JSON with Duffel-specific slice/segment model |
| Error model | `errors[]` array with IATA error codes | `errors[]` with Duffel error types |
| Sandbox | api.sandbox.amadeus.com with test credentials | api.duffel.com with `Duffel-Version` header |

Without an abstraction, Amadeus-specific code and Duffel-specific code would be
scattered across `FlightHunterAgent`, `BookingAgent`, and any other consumer. Adding a
third provider (e.g., Sabre NDC in Phase 2) would require touching multiple agents.

The system also needs:
- **Contract tests** that validate each adapter implementation against the shared
  protocol using recorded VCR fixtures.
- **Per-tenant credentials** — each tenant provides their own Amadeus/Duffel keys.
  The credential injection point must be the adapter, not the agent, so agents remain
  stateless and credential-unaware.
- **Hard call budget enforcement** — the coordinator needs to count provider calls
  uniformly, regardless of which provider is being called.

---

## Decision

We define Python `Protocol` classes for each provider type and implement a concrete
adapter class per provider. All provider interaction flows through these adapters.

### Protocols (defined in `apps/api/src/travel_agent/providers/base.py`)

```python
class FlightProvider(Protocol):
    async def search(self, query: FlightQuery) -> list[FlightOption]: ...
    async def lock_offer(self, option_id: str) -> OfferLock: ...
    async def confirm_booking(
        self, lock: OfferLock, traveler: Traveler, idempotency_key: str
    ) -> BookingResult: ...
    async def cancel_offer(self, lock: OfferLock) -> None: ...

class HotelProvider(Protocol):
    async def search(self, query: HotelQuery) -> list[HotelOption]: ...
    async def lock_offer(self, option_id: str) -> OfferLock: ...
    async def confirm_booking(
        self, lock: OfferLock, traveler: Traveler, idempotency_key: str
    ) -> BookingResult: ...
    async def cancel_offer(self, lock: OfferLock) -> None: ...
```

### Concrete adapters

```
apps/api/src/travel_agent/providers/
├── base.py                  # FlightProvider, HotelProvider Protocols + shared types
├── amadeus_flight.py        # AmadeusFlightProvider(FlightProvider)
├── amadeus_hotel.py         # AmadeusHotelProvider(HotelProvider)
└── duffel_flight.py         # DuffelFlightProvider(FlightProvider)
```

Each adapter is responsible for:
1. **Authentication** — managing its own token lifecycle (refresh for Amadeus OAuth2,
   static bearer for Duffel).
2. **Request construction** — translating `FlightQuery` / `HotelQuery` into the
   provider-specific wire format.
3. **Response normalization** — translating provider responses into `FlightOption[]` /
   `HotelOption[]` (internal canonical schemas).
4. **Error normalization** — wrapping provider-specific errors into `ProviderError`
   (a shared exception hierarchy) so agents handle one error type, not two.
5. **Retry + rate-limit handling** — each adapter handles its own 429s with exponential
   backoff. The coordinator sees either a result or a `ProviderError`.

### Internal canonical schemas

`FlightOption` and `HotelOption` are Pydantic models defined in `providers/base.py`.
They contain only what the optimizer needs:

```python
class FlightOption(BaseModel):
    option_id: str
    provider: str                      # "amadeus" | "duffel"
    tenant_id: UUID
    origin: str                        # IATA
    destination: str                   # IATA
    departure_dt: datetime
    arrival_dt: datetime
    duration_minutes: int
    layover_count: int
    layover_duration_minutes: int
    airline: str
    cabin_class: str
    price_minor: int                   # smallest currency unit
    currency: str
    is_refundable: bool
    refund_penalty_minor: int
    raw_provider_ref: str              # opaque ID for lock_offer
```

The optimizer never sees Amadeus JSON or Duffel JSON — only `FlightOption` objects.

### Credential injection

The `Coordinator` loads the tenant's `ProviderCredential` (decrypted from GCP Secret
Manager via `apps/api/src/travel_agent/tenancy/credentials.py`) and injects it into
the adapter at request time:

```python
flight_provider = AmadeusFlightProvider(credential=tenant_cred.amadeus)
hotel_provider  = AmadeusHotelProvider(credential=tenant_cred.amadeus)
duffel_provider = DuffelFlightProvider(credential=tenant_cred.duffel)
```

Adapters do not read environment variables for credentials. All credential access goes
through `tenancy/credentials.py` to enforce per-tenant isolation.

### Call budget counting

The coordinator wraps each `provider.search()` call with a budget decorator that
increments a counter on `RequestState`. The adapter itself does not manage the budget.
This keeps the budget logic central and provider-agnostic.

---

## Consequences

**Positive:**
- Adding a Sabre NDC adapter in Phase 2 is a single new file
  (`providers/sabre_flight.py`) that implements `FlightProvider`. No agent changes.
- `FlightHunterAgent` and `HotelHunterAgent` are completely provider-agnostic. They
  call `provider.search()` and get back `FlightOption[]`. Their Claude prompts and
  parsing logic are unaffected by which provider is behind the interface.
- Contract tests can validate any adapter against the protocol by running the same
  test suite with different adapter instances:
  ```python
  @pytest.mark.parametrize("provider", [amadeus_flight, duffel_flight])
  async def test_search_returns_flight_options(provider):
      options = await provider.search(sample_query)
      assert all(isinstance(o, FlightOption) for o in options)
  ```
- The VCR.py recorded-fixture approach in contract tests works naturally: record
  once per provider, replay against the Protocol contract.
- Per-tenant credential injection is a single constructor argument per adapter instance.
  The adapter never reaches outside its own constructor for credentials.
- `ProviderError` normalization means the coordinator has one error type to handle,
  not two (or three, post-Phase 2).

**Negative:**
- Normalization loses provider-specific data. For example, Amadeus returns on-time
  performance data; Duffel does not. This data is currently excluded from `FlightOption`
  to maintain a uniform schema. Adding it later requires a schema change and adapter
  updates.
- Each adapter is non-trivial to implement correctly: auth token lifecycle, pagination
  handling, retry logic, and normalization are all per-provider work. This is inherent
  to the multi-provider problem, but the Protocol pattern makes it visible.
- Protocol structural typing (duck typing) means Python will not raise a type error if
  an adapter is missing a method until that method is called. Mypy with `strict = true`
  will catch this, but only if the adapter is explicitly type-annotated as implementing
  the Protocol.

**Neutral:**
- The Protocol approach uses structural subtyping, not inheritance. Adapter classes do
  not inherit from `FlightProvider`. This keeps them independent and avoids method
  resolution order surprises.
- `lock_offer`, `confirm_booking`, and `cancel_offer` are no-ops in the test fixture
  layer. The VCR contract tests focus on `search()` for Phase 1; booking method tests
  run against the Amadeus/Duffel sandboxes in Phase 5.

---

## Alternatives Considered

### Alternative 1: No shared interface — direct SDK usage per provider

Each agent calls provider SDKs directly. `FlightHunterAgent` contains both Amadeus and
Duffel call paths. `BookingAgent` contains both booking paths.

**Rejected because:**
- Adding a third provider in Phase 2 requires modifying `FlightHunterAgent`,
  `BookingAgent`, and potentially `OptimizerAgent` (if provider-specific fields differ).
- Contract testing is impossible — there is no shared interface to test against.
- Credential injection has no natural home; agents would need direct access to tenant
  credentials, blurring the agent/tenancy boundary.
- Amadeus and Duffel error handling would be duplicated across agents, leading to
  inconsistent error surfacing behavior.

### Alternative 2: Generic provider dispatcher

A single `call_provider(provider: str, operation: str, **kwargs) -> dict` function that
routes calls by name:

```python
result = await call_provider("amadeus", "search_flights", query=q)
```

**Rejected because:**
- All type safety is lost. The return type is `dict`, not `FlightOption[]`. Mypy cannot
  validate that `FlightHunterAgent` uses the result correctly.
- IDE support disappears — no autocomplete, no jump-to-definition for provider operations.
- Adding a new operation (e.g., `lock_offer`) requires updating the dispatcher's routing
  table, which is effectively an untyped registry.
- Contract tests cannot be parametric over the interface because there is no interface —
  there is just a function with a string argument.

### Alternative 3: Adapter inheritance from abstract base class

Use `abc.ABC` and `@abstractmethod` instead of `Protocol`:

```python
class FlightProvider(abc.ABC):
    @abstractmethod
    async def search(self, query: FlightQuery) -> list[FlightOption]: ...
```

**Not rejected on principle**, but Protocol was chosen because:
- Structural typing is more idiomatic for Python's duck-typing ecosystem.
- Adapters can satisfy the Protocol without being imported from `providers/base.py`,
  which is useful for test doubles that don't want to inherit from the base.
- Third-party provider libraries that happen to implement the same interface work
  transparently, though no such libraries are anticipated for this project.

---

*Referenced plan.md sections: §4.3, §8.4, §9, §12*
