"""DemoProvider — unified search + bookable provider for the closed-loop demo.

Implements BOTH InventoryProvider (search: flights + hotels) and
BookableInventoryProvider (revalidate / book / cancel) over ONE in-memory
catalog with ONE coherent offer-ID namespace.

A FlightOption.id returned by get_flights() is accepted directly by
revalidate() / book() — no translation required.

ID scheme:
  DEMO-FLT-NNN-YYYY-MM-DD  (hardcoded catalog, window-qualified)
  GEN-{ORIGINDEST}-NNN-YYYY-MM-DD  (generated routes, window-qualified)
  DEMO-HTL-NNN-YYYY-MM-DD  (hotels)

The base offer ID is always 3 dash-separated parts:
  DEMO-FLT-001, GEN-DELDXB-001, DEMO-HTL-001

_base_offer_id() strips the date suffix for all ID shapes.

PRICE-CHANGED TRIGGER (stateful per DemoProvider instance):
  The cheapest economy offer on every route is the price-change trigger.
  - Hardcoded routes: DEMO-FLT-005 (DEL→BOM economy at ₹4,600 → ₹7,200).
  - Generated routes: GEN-{ORIGINDEST}-001 (cheapest economy, 15% increase).
  First revalidate of the trigger offer → price_changed=True.
  Subsequent revalidates → price_changed=False at the settled price.
  First /book attempt stops at the price-confirm step; second /book proceeds.

GENERATED ROUTES — STATELESS BY DESIGN:
  Any origin→destination not in the hardcoded catalog gets 3 deterministic
  offers from _generate_route_offers(), which derives all values from
  md5(origin+destination). Identical input → identical output on every Cloud
  Run instance. When revalidate()/book() arrive with a GEN-* offer_id,
  _ensure_gen_offer() reconstructs the offer from the ID itself — no shared
  state or prior get_flights() call required.

THIS IS A SANDBOX MOCK. No real inventory, no real PNR, no payments.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import uuid
from datetime import UTC, timedelta
from datetime import datetime as _datetime

from travel_agent.coordinator.state import (
    CabinClass,
    FlightOption,
    HotelOption,
    TripType,
    Window,
)
from travel_agent.providers.base import (
    BookingConflictError,
    BookingResult,
    CancellationResult,
    InventoryClientError,
    RevalidationResult,
)

HOLD_TTL_MINUTES: int = 15
# 15% price increase on the first revalidation of the cheapest offer per route.
PRICE_CHANGE_MULTIPLIER: float = 1.15

# ── hardcoded catalog ──────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class _DemoFlight:
    offer_id: str
    origin_iata: str
    destination_iata: str
    airline_code: str
    flight_number: str
    cabin_class: str
    price_inr: int
    depart_hour: int
    depart_minute: int
    duration_minutes: int
    return_duration_minutes: int = 135
    layover_count: int = 0
    is_refundable: bool = False


@dataclasses.dataclass(frozen=True)
class _DemoHotel:
    offer_id: str
    name: str
    city: str
    stars: float
    review_score: float
    price_per_night_inr: int
    location_description: str = ""
    is_refundable: bool = False


_FLIGHT_CATALOG: tuple[_DemoFlight, ...] = (
    _DemoFlight("DEMO-FLT-001", "DEL", "BOM", "AI", "AI-101", "economy", 4_850, 6, 30, 135),
    _DemoFlight("DEMO-FLT-002", "DEL", "BOM", "6E", "6E-201", "business", 14_200, 10, 0, 135),
    _DemoFlight("DEMO-FLT-003", "BOM", "GOI", "SG", "SG-301", "economy", 3_200, 11, 30, 75),
    _DemoFlight("DEMO-FLT-004", "DEL", "BLR", "AI", "AI-401", "economy", 5_600, 14, 0, 150),
    _DemoFlight("DEMO-FLT-005", "DEL", "BOM", "6E", "6E-501", "economy", 4_600, 19, 0, 135),
)

_HOTEL_CATALOG: tuple[_DemoHotel, ...] = (
    _DemoHotel("DEMO-HTL-001", "Taj Mahal Palace", "Mumbai", 5.0, 9.2, 18_000, "Colaba waterfront"),
    _DemoHotel("DEMO-HTL-002", "Lemon Tree Premier", "Delhi", 3.0, 7.8, 4_500, "Aerocity"),
    _DemoHotel("DEMO-HTL-003", "Fortune Select Exotica", "Goa", 4.0, 8.1, 7_500, "Majorda beach"),
)

_FLIGHT_INDEX: dict[str, _DemoFlight] = {f.offer_id: f for f in _FLIGHT_CATALOG}
_HOTEL_INDEX: dict[str, _DemoHotel] = {h.offer_id: h for h in _HOTEL_CATALOG}

PRICE_CHANGE_OFFER_ID = "DEMO-FLT-005"
PRICE_CHANGE_ORIGINAL_PRICE = 4_600
PRICE_CHANGE_NEW_PRICE = 7_200

# Base offer ID is always the first 3 dash-separated parts.
_OFFER_ID_BASE_PARTS: int = 3

# ── route classification ───────────────────────────────────────────────────────

_INDIA_AIRPORTS: frozenset[str] = frozenset([
    "DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "AMD", "GOI", "COK", "PNQ",
    "JAI", "LKO", "BBI", "GAU", "IXC", "ATQ", "SXR",
])
_GCC_AIRPORTS: frozenset[str] = frozenset(["DXB", "AUH", "DOH", "KWI", "BAH", "MCT", "RUH", "SHJ"])
_SEA_AIRPORTS: frozenset[str] = frozenset(["SIN", "KUL", "BKK", "CGK", "MNL", "SGN"])
_EUR_AIRPORTS: frozenset[str] = frozenset(["CDG", "LHR", "FRA", "AMS", "ZRH", "FCO", "BCN", "MUC"])
_EASIA_AIRPORTS: frozenset[str] = frozenset(["NRT", "HND", "ICN", "PEK", "PVG", "HKG", "TPE"])
_AMER_AIRPORTS: frozenset[str] = frozenset(["JFK", "EWR", "ORD", "LAX", "YYZ", "GRU"])

# (airline_code, flight_number_prefix)
_ECONOMY_AIRLINES: tuple[tuple[str, str], ...] = (
    ("AI", "AI"), ("6E", "6E"), ("SG", "SG"), ("UK", "UK"),
    ("EK", "EK"), ("QR", "QR"), ("G9", "G9"), ("SQ", "SQ"),
)
_BUSINESS_AIRLINES: tuple[tuple[str, str], ...] = (
    ("AI", "AI"), ("EK", "EK"), ("QR", "QR"), ("SQ", "SQ"),
)
_DEP_MINUTES: tuple[int, ...] = (0, 15, 30, 45)


_ROUTE_RANGE_TABLE: tuple[tuple[frozenset[str], tuple[int, int, int]], ...] = (
    (_GCC_AIRPORTS,   (7_000,  22_000, 210)),
    (_SEA_AIRPORTS,   (10_000, 30_000, 360)),
    (_EASIA_AIRPORTS, (18_000, 60_000, 420)),
    (_EUR_AIRPORTS,   (28_000, 85_000, 540)),
    (_AMER_AIRPORTS,  (45_000, 120_000, 900)),
)
_DOMESTIC_RANGE: tuple[int, int, int] = (2_500, 9_000, 120)
_FALLBACK_RANGE: tuple[int, int, int] = (12_000, 40_000, 300)


def _route_range(origin: str, destination: str) -> tuple[int, int, int]:
    """Return (eco_min_inr, eco_max_inr, flight_duration_minutes) for the route."""
    if origin in _INDIA_AIRPORTS and destination in _INDIA_AIRPORTS:
        return _DOMESTIC_RANGE
    pair = {origin, destination}
    for airport_set, price_range in _ROUTE_RANGE_TABLE:
        if pair & airport_set:
            return price_range
    return _FALLBACK_RANGE


def _lcg(seed: int) -> tuple[int, int]:
    """Minimal LCG. Returns (next_seed, value)."""
    s = (seed * 1_664_525 + 1_013_904_223) & 0xFFFF_FFFF
    return s, s


@functools.lru_cache(maxsize=512)
def _generate_route_offers(origin: str, destination: str) -> tuple[_DemoFlight, ...]:
    """Return 3 deterministic offers for any origin→destination.

    Derives all values from md5(origin+destination) — identical on every
    process/instance, so booking never fails when Cloud Run routes /book to
    a different instance than /search.
    """
    seed = int.from_bytes(
        hashlib.md5(f"{origin}{destination}".encode(), usedforsecurity=False).digest()[:4],
        "big",
    )
    eco_min, eco_max, duration = _route_range(origin, destination)

    seed, r = _lcg(seed)
    eco1_price = round((eco_min + r % (eco_max - eco_min)) / 100) * 100
    seed, r = _lcg(seed)
    eco2_price = round((eco1_price + 1_000 + r % 3_000) / 100) * 100
    seed, r = _lcg(seed)
    biz_mult_x10 = 25 + r % 16  # 2.5x - 4.0x economy multiplier
    biz_price = round(eco1_price * biz_mult_x10 / 10 / 100) * 100

    seed, r = _lcg(seed)
    eco1_al = _ECONOMY_AIRLINES[r % len(_ECONOMY_AIRLINES)]
    seed, r = _lcg(seed)
    eco2_pool = [a for a in _ECONOMY_AIRLINES if a != eco1_al]
    eco2_al = eco2_pool[r % len(eco2_pool)]
    seed, r = _lcg(seed)
    biz_al = _BUSINESS_AIRLINES[r % len(_BUSINESS_AIRLINES)]

    seed, r = _lcg(seed)
    eco1_dh = 6 + r % 13       # 06-18
    seed, r = _lcg(seed)
    eco2_dh = (eco1_dh + 3 + r % 8) % 24
    seed, r = _lcg(seed)
    biz_dh = 8 + r % 12        # 08-19

    seed, r = _lcg(seed)
    eco1_dm = _DEP_MINUTES[r % 4]
    seed, r = _lcg(seed)
    eco2_dm = _DEP_MINUTES[r % 4]
    seed, r = _lcg(seed)
    biz_dm = _DEP_MINUTES[r % 4]

    seed, r = _lcg(seed)
    eco1_fn = 100 + r % 900
    seed, r = _lcg(seed)
    eco2_fn = 100 + r % 900
    seed, r = _lcg(seed)
    biz_fn = 100 + r % 900

    rk = f"{origin}{destination}"
    return_dur = max(duration - 15, 60)
    return (
        _DemoFlight(
            f"GEN-{rk}-001", origin, destination,
            eco1_al[0], f"{eco1_al[1]}-{eco1_fn}",
            "economy", eco1_price, eco1_dh, eco1_dm, duration, return_dur,
        ),
        _DemoFlight(
            f"GEN-{rk}-002", origin, destination,
            eco2_al[0], f"{eco2_al[1]}-{eco2_fn}",
            "economy", eco2_price, eco2_dh, eco2_dm, duration, return_dur,
        ),
        _DemoFlight(
            f"GEN-{rk}-003", origin, destination,
            biz_al[0], f"{biz_al[1]}-{biz_fn}",
            "business", biz_price, biz_dh, biz_dm, duration, return_dur,
        ),
    )


# Module-level index for generated offers — populated lazily, persistent per process.
# Stateless reconstruction is always available via _ensure_gen_offer().
_GENERATED_FLIGHT_INDEX: dict[str, _DemoFlight] = {}
# Cheapest economy base IDs per generated route — the price-change triggers.
_GENERATED_PRICE_CHANGE_OFFER_IDS: set[str] = set()


def _register_generated_route(origin: str, destination: str) -> None:
    """Populate _GENERATED_FLIGHT_INDEX and _GENERATED_PRICE_CHANGE_OFFER_IDS for a route."""
    offers = _generate_route_offers(origin, destination)
    for f in offers:
        _GENERATED_FLIGHT_INDEX[f.offer_id] = f
    eco = [f for f in offers if f.cabin_class == "economy"]
    if eco:
        _GENERATED_PRICE_CHANGE_OFFER_IDS.add(min(eco, key=lambda f: f.price_inr).offer_id)


def _ensure_gen_offer(base_id: str) -> None:
    """Lazily register a GEN-* offer by reconstructing the route from the offer ID.

    Called during revalidate()/book() so those paths are fully stateless —
    the offer is reproducible on any Cloud Run instance without a prior
    get_flights() call in the same process.
    """
    if base_id in _GENERATED_FLIGHT_INDEX:
        return
    parts = base_id.split("-")
    if len(parts) == 3 and parts[0] == "GEN" and len(parts[1]) == 6:  # noqa: PLR2004
        _register_generated_route(parts[1][:3], parts[1][3:])


def _compute_settled_price(original_price: int) -> int:
    """Settled (post-change) price: 15% increase, rounded to nearest ₹100."""
    return round(original_price * PRICE_CHANGE_MULTIPLIER / 100) * 100


# ── internal hold state ────────────────────────────────────────────────────────


@dataclasses.dataclass
class _HoldRecord:
    offer_id: str
    idempotency_key: str
    result: BookingResult
    cancelled: bool = False


# ── provider ──────────────────────────────────────────────────────────────────


def _base_offer_id(offer_id: str) -> str:
    """Extract the base offer ID (first 3 dash-separated parts).

    "DEMO-FLT-001-2024-03-15" -> "DEMO-FLT-001"
    "GEN-DELDXB-001-2024-03-15" -> "GEN-DELDXB-001"
    "DEMO-FLT-001" -> "DEMO-FLT-001"  (already bare)
    """
    parts = offer_id.split("-")
    if len(parts) >= _OFFER_ID_BASE_PARTS:
        return "-".join(parts[:_OFFER_ID_BASE_PARTS])
    return offer_id


class DemoProvider:
    """Unified search + bookable provider for the closed-loop demo.

    THIS IS A SANDBOX MOCK. No real inventory, payments, or PNRs.
    """

    def __init__(self) -> None:
        self._holds: dict[str, _HoldRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        # Tracks which base offer IDs have already shown the price-change trigger
        # in this process instance. Instance-level so a fresh DemoProvider (tests)
        # starts with a clean price-change slate.
        self._price_changed_shown: set[str] = set()

    # ── InventoryProvider ──────────────────────────────────────────────────

    async def close(self) -> None:
        self._holds.clear()
        self._idempotency_index.clear()
        self._price_changed_shown.clear()

    # ── Search ────────────────────────────────────────────────────────────

    def get_flights(
        self,
        origin: str,
        destination: str,
        window: Window,
        *,
        trip_type: TripType = TripType.ROUND_TRIP,
        trip_duration_days: int = 7,
    ) -> list[FlightOption]:
        """Return demo flights for any origin→destination.

        Uses the hardcoded catalog when a route is listed there; otherwise
        generates 3 deterministic offers from _generate_route_offers().
        """
        is_one_way = trip_type == TripType.ONE_WAY

        catalog = [
            f for f in _FLIGHT_CATALOG
            if f.origin_iata == origin and f.destination_iata == destination
        ]
        if not catalog:
            _register_generated_route(origin, destination)
            catalog = [
                f for f in _GENERATED_FLIGHT_INDEX.values()
                if f.origin_iata == origin and f.destination_iata == destination
            ]

        results: list[FlightOption] = []
        for tmpl in catalog:
            dep = _datetime(
                window.start_date.year,
                window.start_date.month,
                window.start_date.day,
                tmpl.depart_hour,
                tmpl.depart_minute,
                tzinfo=UTC,
            )
            arr = dep + timedelta(minutes=tmpl.duration_minutes)
            if is_one_way:
                price = round(tmpl.price_inr * 0.58)
                ret_dep_str: str | None = None
                ret_arr_str: str | None = None
                ret_dur: int | None = None
            else:
                price = tmpl.price_inr
                ret_date = window.start_date + timedelta(days=trip_duration_days)
                ret_dep = _datetime(ret_date.year, ret_date.month, ret_date.day, 10, 0, tzinfo=UTC)
                ret_dur = tmpl.return_duration_minutes
                ret_arr = ret_dep + timedelta(minutes=ret_dur)
                ret_dep_str = ret_dep.isoformat()
                ret_arr_str = ret_arr.isoformat()

            offer_id = f"{tmpl.offer_id}-{window.start_date.isoformat()}"
            results.append(
                FlightOption(
                    id=offer_id,
                    window=window,
                    provider="demo",
                    origin_iata=origin,
                    destination_iata=destination,
                    outbound_departure_at=dep.isoformat(),
                    outbound_arrival_at=arr.isoformat(),
                    return_departure_at=ret_dep_str,
                    return_arrival_at=ret_arr_str,
                    airline_code=tmpl.airline_code,
                    flight_number=tmpl.flight_number,
                    cabin_class=CabinClass(tmpl.cabin_class),
                    price_inr=price,
                    outbound_duration_minutes=tmpl.duration_minutes,
                    return_duration_minutes=ret_dur,
                    layover_count=tmpl.layover_count,
                    is_refundable=tmpl.is_refundable,
                )
            )
        return results

    def get_hotels(
        self,
        city: str,
        window: Window,
        nights: int,
        min_stars: float = 0.0,
    ) -> list[HotelOption]:
        """Return demo hotels matching city and star filter for the given window."""
        results: list[HotelOption] = []
        for tmpl in _HOTEL_CATALOG:
            if tmpl.city != city or tmpl.stars < min_stars:
                continue
            offer_id = f"{tmpl.offer_id}-{window.start_date.isoformat()}"
            results.append(
                HotelOption(
                    id=offer_id,
                    window=window,
                    provider="demo",
                    name=tmpl.name,
                    city=tmpl.city,
                    stars=tmpl.stars,
                    review_score=tmpl.review_score,
                    price_per_night_inr=tmpl.price_per_night_inr,
                    total_price_inr=tmpl.price_per_night_inr * nights,
                    location_description=tmpl.location_description,
                    is_refundable=tmpl.is_refundable,
                )
            )
        return results

    # ── BookableInventoryProvider ──────────────────────────────────────────

    async def revalidate(self, offer_id: str) -> RevalidationResult:
        """Re-confirm price and availability.

        The cheapest economy offer on every route is the price-change trigger:
        first call returns price_changed=True; subsequent calls settle at the
        new price. Works on both hardcoded and generated offer IDs.
        """
        base = _base_offer_id(offer_id)
        _ensure_gen_offer(base)
        self._require_offer(base)

        is_trigger = base == PRICE_CHANGE_OFFER_ID or base in _GENERATED_PRICE_CHANGE_OFFER_IDS

        if is_trigger and base not in self._price_changed_shown:
            self._price_changed_shown.add(base)
            original = (
                PRICE_CHANGE_ORIGINAL_PRICE
                if base == PRICE_CHANGE_OFFER_ID
                else self._offer_price(base)
            )
            new = (
                PRICE_CHANGE_NEW_PRICE
                if base == PRICE_CHANGE_OFFER_ID
                else _compute_settled_price(original)
            )
            return RevalidationResult(
                offer_id=offer_id,
                current_price_inr=new,
                is_available=True,
                price_changed=True,
                previous_price_inr=original,
            )

        if base == PRICE_CHANGE_OFFER_ID:
            current = PRICE_CHANGE_NEW_PRICE
        elif is_trigger:
            current = _compute_settled_price(self._offer_price(base))
        else:
            current = self._offer_price(base)

        return RevalidationResult(
            offer_id=offer_id,
            current_price_inr=current,
            is_available=True,
            price_changed=False,
        )

    async def book(self, offer_id: str, idempotency_key: str) -> BookingResult:
        """Create a demo hold. Idempotent; raises BookingConflictError on key reuse."""
        if idempotency_key in self._idempotency_index:
            pnr = self._idempotency_index[idempotency_key]
            record = self._holds[pnr]
            if record.offer_id != offer_id:
                msg = (
                    f"Idempotency key {idempotency_key!r} is already bound to "
                    f"offer {record.offer_id!r}; cannot reuse for {offer_id!r}."
                )
                raise BookingConflictError(msg)
            return record.result

        base = _base_offer_id(offer_id)
        _ensure_gen_offer(base)
        self._require_offer(base)

        pnr = f"DEMO-PNR-{uuid.uuid4().hex[:8].upper()}"
        offer_lock_id = f"DEMO-LOCK-{uuid.uuid4().hex[:8].upper()}"
        hold_expires_at = (_datetime.now(UTC) + timedelta(minutes=HOLD_TTL_MINUTES)).isoformat()

        result = BookingResult(
            pnr=pnr,
            offer_lock_id=offer_lock_id,
            hold_expires_at=hold_expires_at,
            idempotency_key=idempotency_key,
        )
        record = _HoldRecord(offer_id=offer_id, idempotency_key=idempotency_key, result=result)
        self._holds[pnr] = record
        self._idempotency_index[idempotency_key] = pnr
        return result

    async def cancel(self, booking_ref: str) -> CancellationResult:
        """Release a hold. Returns cancelled=False if booking_ref is unknown."""
        if booking_ref not in self._holds:
            return CancellationResult(booking_ref=booking_ref, cancelled=False)
        self._holds[booking_ref].cancelled = True
        return CancellationResult(booking_ref=booking_ref, cancelled=True)

    # ── Internal helpers ───────────────────────────────────────────────────

    def _require_offer(self, base_id: str) -> None:
        if (
            base_id not in _FLIGHT_INDEX
            and base_id not in _HOTEL_INDEX
            and base_id not in _GENERATED_FLIGHT_INDEX
        ):
            msg = f"[DEMO] Unknown offer_id: {base_id!r}"
            raise InventoryClientError(msg)

    def _offer_price(self, base_id: str) -> int:
        if base_id in _FLIGHT_INDEX:
            return _FLIGHT_INDEX[base_id].price_inr
        if base_id in _GENERATED_FLIGHT_INDEX:
            return _GENERATED_FLIGHT_INDEX[base_id].price_inr
        return _HOTEL_INDEX[base_id].price_per_night_inr
