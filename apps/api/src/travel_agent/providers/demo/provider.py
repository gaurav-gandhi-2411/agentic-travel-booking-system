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

PRICE-CHANGED TRIGGER (stateless):
  The cheapest NON-STOP economy offer on every route is the price-change trigger.
  - Hardcoded routes: DEMO-FLT-005 (DEL→BOM economy at ₹4,600 → ₹7,200).
  - Generated routes: the cheapest non-stop economy GEN-*-001 (15% increase).
  revalidate() always returns price_changed=True for trigger offers — no per-
  instance state. The halt/proceed decision lives in the coordinator via the
  accept_price_change flag on /book.

GENERATED ROUTES — STATELESS BY DESIGN:
  Any origin->destination not in the hardcoded catalog gets 3-4 deterministic
  offers from _generate_route_offers(), which derives all values from
  md5(origin+destination). Identical input → identical output on every Cloud
  Run instance. Corridor-appropriate airlines, departure times, and fare spreads.
  Routes >5h also get a 4th 1-stop economy option at a modest discount.

CANCEL — CROSS-INSTANCE SAFE (HMAC-SIGNED PNR):
  cancel() first checks the local _holds dict (same-instance, exact). On a
  cross-instance miss it cryptographically verifies the PNR: DemoProvider.book()
  issues DEMO-PNR-{8 hex} where the 8 chars encode a 4-byte random payload
  followed by 4 bytes of HMAC-SHA256(_PNR_HMAC_SECRET, payload). A legitimately
  issued PNR verifies on any Cloud Run instance (probability of forgery: 1/2^32).
  Wrong format, tampered bytes, or never-issued refs return cancelled=False.

THIS IS A SANDBOX MOCK. No real inventory, no real PNR, no payments.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import hmac
import re
import secrets
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
# 15% price increase on the first revalidation of the cheapest non-stop offer.
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

_INDIA_AIRPORTS: frozenset[str] = frozenset(
    [
        "DEL",
        "BOM",
        "BLR",
        "MAA",
        "HYD",
        "CCU",
        "AMD",
        "GOI",
        "COK",
        "PNQ",
        "JAI",
        "LKO",
        "BBI",
        "GAU",
        "IXC",
        "ATQ",
        "SXR",
    ]
)
_GCC_AIRPORTS: frozenset[str] = frozenset(["DXB", "AUH", "DOH", "KWI", "BAH", "MCT", "RUH", "SHJ"])
_SEA_AIRPORTS: frozenset[str] = frozenset(["SIN", "KUL", "BKK", "CGK", "MNL", "SGN"])
_EUR_AIRPORTS: frozenset[str] = frozenset(["CDG", "LHR", "FRA", "AMS", "ZRH", "FCO", "BCN", "MUC"])
_EASIA_AIRPORTS: frozenset[str] = frozenset(["NRT", "HND", "ICN", "PEK", "PVG", "HKG", "TPE"])
_AMER_AIRPORTS: frozenset[str] = frozenset(["JFK", "EWR", "ORD", "LAX", "YYZ", "GRU"])

_DEP_MINUTES: tuple[int, ...] = (0, 15, 30, 45)

# ── corridor profiles ──────────────────────────────────────────────────────────
# Realistic airline pools, departure-hour ranges, and business-class multipliers
# keyed by route corridor. The md5 seed picks within each pool deterministically.


@dataclasses.dataclass(frozen=True)
class _CorridorProfile:
    eco_airlines: tuple[tuple[str, str], ...]  # (IATA code, flight-number prefix)
    biz_airlines: tuple[tuple[str, str], ...]
    dep_hours: tuple[int, ...]  # realistic departure hours
    biz_mult_min_x10: int  # min business multiplier x10
    biz_mult_range_x10: int  # LCG range (max = min + range - 1)
    has_stopover: bool  # generate 4th 1-stop economy option
    stopover_airlines: tuple[tuple[str, str], ...] = ()


# Domestic Indian routes: Indian carriers only, daytime spread, lower biz premium.
_DOMESTIC_INDIA_PROFILE = _CorridorProfile(
    eco_airlines=(("AI", "AI"), ("6E", "6E"), ("SG", "SG"), ("UK", "UK"), ("IX", "IX")),
    biz_airlines=(("AI", "AI"), ("UK", "UK"), ("6E", "6E")),
    dep_hours=(6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21),
    biz_mult_min_x10=18,
    biz_mult_range_x10=8,  # 1.8x - 2.5x
    has_stopover=False,
)

# Gulf corridor: overnight and red-eye popular; Indian + Gulf carriers.
_GULF_PROFILE = _CorridorProfile(
    eco_airlines=(("6E", "6E"), ("AI", "AI"), ("G9", "G9"), ("EK", "EK"), ("QR", "QR")),
    biz_airlines=(("EK", "EK"), ("QR", "QR"), ("EY", "EY"), ("AI", "AI")),
    dep_hours=(1, 2, 3, 6, 8, 10, 14, 18, 20, 23),
    biz_mult_min_x10=25,
    biz_mult_range_x10=16,  # 2.5x - 4.0x
    has_stopover=False,  # 3-4 h; no stopover needed
)

# Southeast Asia: hub carriers + Indian; mix of overnight and daytime.
_SEA_PROFILE = _CorridorProfile(
    eco_airlines=(("AI", "AI"), ("SQ", "SQ"), ("MH", "MH"), ("TG", "TG"), ("6E", "6E")),
    biz_airlines=(("SQ", "SQ"), ("MH", "MH"), ("TG", "TG"), ("AI", "AI")),
    dep_hours=(0, 1, 8, 10, 12, 14, 20, 22),
    biz_mult_min_x10=25,
    biz_mult_range_x10=21,  # 2.5x - 4.5x
    has_stopover=True,
    stopover_airlines=(("SQ", "SQ"), ("MH", "MH")),
)

# Europe: longhaul; Gulf hubs popular; red-eye and early-morning dominant.
_EUROPE_PROFILE = _CorridorProfile(
    eco_airlines=(("AI", "AI"), ("EK", "EK"), ("QR", "QR"), ("LH", "LH")),
    biz_airlines=(("AI", "AI"), ("EK", "EK"), ("QR", "QR"), ("LH", "LH")),
    dep_hours=(1, 2, 3, 8, 14, 22, 23),
    biz_mult_min_x10=30,
    biz_mult_range_x10=21,  # 3.0x - 5.0x
    has_stopover=True,
    stopover_airlines=(("EK", "EK"), ("QR", "QR")),
)

# East Asia: longhaul via Gulf or SEA hubs; overnight popular.
_EASIA_PROFILE = _CorridorProfile(
    eco_airlines=(("AI", "AI"), ("EK", "EK"), ("SQ", "SQ"), ("CX", "CX"), ("MH", "MH")),
    biz_airlines=(("AI", "AI"), ("EK", "EK"), ("SQ", "SQ"), ("CX", "CX")),
    dep_hours=(0, 1, 9, 11, 22, 23),
    biz_mult_min_x10=30,
    biz_mult_range_x10=21,  # 3.0x - 5.0x
    has_stopover=True,
    stopover_airlines=(("SQ", "SQ"), ("CX", "CX")),
)

# Americas: ultra-longhaul; Gulf-hub connections dominate; high biz premium.
_AMER_PROFILE = _CorridorProfile(
    eco_airlines=(("AI", "AI"), ("EK", "EK"), ("QR", "QR"), ("UA", "UA")),
    biz_airlines=(("AI", "AI"), ("EK", "EK"), ("QR", "QR")),
    dep_hours=(1, 2, 22, 23, 8),
    biz_mult_min_x10=35,
    biz_mult_range_x10=26,  # 3.5x - 6.0x
    has_stopover=True,
    stopover_airlines=(("EK", "EK"), ("QR", "QR")),
)

# Fallback for routes that don't match any known corridor.
_FALLBACK_PROFILE = _CorridorProfile(
    eco_airlines=(("AI", "AI"), ("EK", "EK"), ("QR", "QR"), ("SQ", "SQ")),
    biz_airlines=(("AI", "AI"), ("EK", "EK"), ("QR", "QR")),
    dep_hours=(1, 6, 8, 10, 14, 20, 22),
    biz_mult_min_x10=25,
    biz_mult_range_x10=21,  # 2.5x - 4.5x
    has_stopover=True,
    stopover_airlines=(("EK", "EK"), ("QR", "QR")),
)

_CORRIDOR_TABLE: tuple[tuple[frozenset[str], _CorridorProfile], ...] = (
    (_GCC_AIRPORTS, _GULF_PROFILE),
    (_SEA_AIRPORTS, _SEA_PROFILE),
    (_EASIA_AIRPORTS, _EASIA_PROFILE),
    (_EUR_AIRPORTS, _EUROPE_PROFILE),
    (_AMER_AIRPORTS, _AMER_PROFILE),
)

_ROUTE_RANGE_TABLE: tuple[tuple[frozenset[str], tuple[int, int, int]], ...] = (
    (_GCC_AIRPORTS, (7_000, 22_000, 210)),
    (_SEA_AIRPORTS, (10_000, 30_000, 360)),
    (_EASIA_AIRPORTS, (18_000, 60_000, 420)),
    (_EUR_AIRPORTS, (28_000, 85_000, 540)),
    (_AMER_AIRPORTS, (45_000, 120_000, 900)),
)
_DOMESTIC_RANGE: tuple[int, int, int] = (2_500, 9_000, 120)
_FALLBACK_RANGE: tuple[int, int, int] = (12_000, 40_000, 300)

# Routes longer than this get a 4th 1-stop economy option at a modest discount.
_STOPOVER_THRESHOLD_MINUTES: int = 300
_STOPOVER_PRICE_FACTOR: float = 0.82  # 18% cheaper than cheapest non-stop


def _route_range(origin: str, destination: str) -> tuple[int, int, int]:
    """Return (eco_min_inr, eco_max_inr, flight_duration_minutes) for the route."""
    if origin in _INDIA_AIRPORTS and destination in _INDIA_AIRPORTS:
        return _DOMESTIC_RANGE
    pair = {origin, destination}
    for airport_set, price_range in _ROUTE_RANGE_TABLE:
        if pair & airport_set:
            return price_range
    return _FALLBACK_RANGE


def _route_corridor(origin: str, destination: str) -> _CorridorProfile:
    """Return the corridor profile for route-appropriate airline/timing selection."""
    if origin in _INDIA_AIRPORTS and destination in _INDIA_AIRPORTS:
        return _DOMESTIC_INDIA_PROFILE
    pair = {origin, destination}
    for airport_set, profile in _CORRIDOR_TABLE:
        if pair & airport_set:
            return profile
    return _FALLBACK_PROFILE


def _lcg(seed: int) -> tuple[int, int]:
    """Minimal LCG. Returns (next_seed, value)."""
    s = (seed * 1_664_525 + 1_013_904_223) & 0xFFFF_FFFF
    return s, s


@functools.lru_cache(maxsize=512)
def _generate_route_offers(origin: str, destination: str) -> tuple[_DemoFlight, ...]:  # noqa: PLR0915
    """Return 3-4 deterministic offers for any origin->destination.

    Uses corridor-appropriate airlines, departure times, and business-class
    premiums. Routes >5 h also generate a 4th 1-stop economy option priced
    at 18% below the cheapest non-stop economy (hub connections are cheaper
    but less convenient).

    The price-change trigger is the cheapest NON-STOP economy offer only —
    the stopover option is excluded from the trigger set.

    Identical origin+destination always produces identical output on every
    Cloud Run instance (deterministic from md5 seed).
    """
    seed = int.from_bytes(
        hashlib.md5(f"{origin}{destination}".encode(), usedforsecurity=False).digest()[:4],
        "big",
    )
    eco_min, eco_max, duration = _route_range(origin, destination)
    corridor = _route_corridor(origin, destination)

    # Economy prices (non-stop)
    seed, r = _lcg(seed)
    eco1_price = round((eco_min + r % (eco_max - eco_min)) / 100) * 100
    seed, r = _lcg(seed)
    eco2_price = round((eco1_price + 1_000 + r % 3_000) / 100) * 100
    seed, r = _lcg(seed)
    biz_mult_x10 = corridor.biz_mult_min_x10 + r % corridor.biz_mult_range_x10
    biz_price = round(eco1_price * biz_mult_x10 / 10 / 100) * 100

    # Airlines from corridor-appropriate pools
    seed, r = _lcg(seed)
    eco1_al = corridor.eco_airlines[r % len(corridor.eco_airlines)]
    seed, r = _lcg(seed)
    eco2_pool = [a for a in corridor.eco_airlines if a != eco1_al]
    eco2_al = eco2_pool[r % len(eco2_pool)]
    seed, r = _lcg(seed)
    biz_al = corridor.biz_airlines[r % len(corridor.biz_airlines)]

    # Departure hours from corridor-appropriate range
    seed, r = _lcg(seed)
    eco1_dh = corridor.dep_hours[r % len(corridor.dep_hours)]
    seed, r = _lcg(seed)
    eco2_dh = corridor.dep_hours[r % len(corridor.dep_hours)]
    seed, r = _lcg(seed)
    biz_dh = corridor.dep_hours[r % len(corridor.dep_hours)]

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

    offers: list[_DemoFlight] = [
        _DemoFlight(
            f"GEN-{rk}-001",
            origin,
            destination,
            eco1_al[0],
            f"{eco1_al[1]}-{eco1_fn}",
            "economy",
            eco1_price,
            eco1_dh,
            eco1_dm,
            duration,
            return_dur,
        ),
        _DemoFlight(
            f"GEN-{rk}-002",
            origin,
            destination,
            eco2_al[0],
            f"{eco2_al[1]}-{eco2_fn}",
            "economy",
            eco2_price,
            eco2_dh,
            eco2_dm,
            duration,
            return_dur,
        ),
        _DemoFlight(
            f"GEN-{rk}-003",
            origin,
            destination,
            biz_al[0],
            f"{biz_al[1]}-{biz_fn}",
            "business",
            biz_price,
            biz_dh,
            biz_dm,
            duration,
            return_dur,
        ),
    ]

    # 4th 1-stop economy option for routes >5 h with a known hub carrier.
    if (
        corridor.has_stopover
        and duration > _STOPOVER_THRESHOLD_MINUTES
        and corridor.stopover_airlines
    ):
        seed, r = _lcg(seed)
        stop_al = corridor.stopover_airlines[r % len(corridor.stopover_airlines)]
        seed, r = _lcg(seed)
        stop_fn = 100 + r % 900
        seed, r = _lcg(seed)
        stop_dh = corridor.dep_hours[r % len(corridor.dep_hours)]
        seed, r = _lcg(seed)
        stop_dm = _DEP_MINUTES[r % 4]
        stop_price = round(eco1_price * _STOPOVER_PRICE_FACTOR / 100) * 100
        stop_duration = duration + 90  # +90 min for the hub connection
        offers.append(
            _DemoFlight(
                f"GEN-{rk}-004",
                origin,
                destination,
                stop_al[0],
                f"{stop_al[1]}-{stop_fn}",
                "economy",
                stop_price,
                stop_dh,
                stop_dm,
                stop_duration,
                return_dur + 90,
                layover_count=1,
            )
        )

    return tuple(offers)


# Module-level index for generated offers — populated lazily, persistent per process.
# Stateless reconstruction is always available via _ensure_gen_offer().
_GENERATED_FLIGHT_INDEX: dict[str, _DemoFlight] = {}
# Cheapest NON-STOP economy base IDs per generated route — the price-change triggers.
# Stopover (layover_count > 0) offers are excluded so the trigger is predictable.
_GENERATED_PRICE_CHANGE_OFFER_IDS: set[str] = set()


def _register_generated_route(origin: str, destination: str) -> None:
    """Populate _GENERATED_FLIGHT_INDEX and _GENERATED_PRICE_CHANGE_OFFER_IDS for a route."""
    offers = _generate_route_offers(origin, destination)
    for f in offers:
        _GENERATED_FLIGHT_INDEX[f.offer_id] = f
    # Trigger is cheapest non-stop economy only — excludes the 1-stop discount option.
    non_stop_eco = [f for f in offers if f.cabin_class == "economy" and f.layover_count == 0]
    if non_stop_eco:
        _GENERATED_PRICE_CHANGE_OFFER_IDS.add(min(non_stop_eco, key=lambda f: f.price_inr).offer_id)


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


# ── PNR signing — HMAC self-verifying token ────────────────────────────────────
# PNRs are random (not derivable from booking inputs) and cancel() receives only
# the booking_ref — not the original offer_id or idempotency_key. True "reconstruct
# from booking" isn't feasible statelessly. Instead we use a signed token: the 16-char
# hex field encodes 4 random bytes (payload) + 4-byte HMAC-SHA256 tag. Any PNR issued
# by this system verifies on any Cloud Run instance. Forgery probability: 1/2^32.
_PNR_HMAC_SECRET: bytes = b"dealhunter-demo-pnr-v1"
# Outer format gate before attempting HMAC — fast rejection of garbage refs.
# 16 hex chars = 8 bytes = 4-byte payload + 4-byte HMAC-SHA256 tag.
_DEMO_PNR_RE: re.Pattern[str] = re.compile(r"^DEMO-PNR-[0-9A-F]{16}$")


def _issue_pnr_token() -> str:
    """Generate a 16-char uppercase hex PNR token: 4-byte random payload + 4-byte HMAC tag."""
    payload = secrets.token_bytes(4)
    tag = hmac.digest(_PNR_HMAC_SECRET, payload, "sha256")[:4]
    return (payload + tag).hex().upper()


def _verify_pnr_token(token: str) -> bool:
    """Return True if token was produced by _issue_pnr_token() (stateless, no stored state)."""
    if len(token) != 16:  # noqa: PLR2004
        return False
    try:
        raw = bytes.fromhex(token)
    except ValueError:
        return False
    payload, supplied_tag = raw[:4], raw[4:]
    expected_tag = hmac.digest(_PNR_HMAC_SECRET, payload, "sha256")[:4]
    return hmac.compare_digest(expected_tag, supplied_tag)


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

    # ── InventoryProvider ──────────────────────────────────────────────────

    async def close(self) -> None:
        self._holds.clear()
        self._idempotency_index.clear()

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
        generates 3-4 deterministic offers from _generate_route_offers().
        """
        is_one_way = trip_type == TripType.ONE_WAY

        catalog = [
            f
            for f in _FLIGHT_CATALOG
            if f.origin_iata == origin and f.destination_iata == destination
        ]
        if not catalog:
            _register_generated_route(origin, destination)
            catalog = [
                f
                for f in _GENERATED_FLIGHT_INDEX.values()
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

        The cheapest NON-STOP economy offer on every route is the price-change
        trigger: revalidate() always returns price_changed=True for those offers
        (stateless — no per-instance _price_changed_shown state). The
        halt/proceed decision lives in the coordinator via accept_price_change.
        """
        base = _base_offer_id(offer_id)
        _ensure_gen_offer(base)
        self._require_offer(base)

        is_trigger = base == PRICE_CHANGE_OFFER_ID or base in _GENERATED_PRICE_CHANGE_OFFER_IDS

        if is_trigger:
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

        return RevalidationResult(
            offer_id=offer_id,
            current_price_inr=self._offer_price(base),
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

        pnr = f"DEMO-PNR-{_issue_pnr_token()}"
        offer_lock_id = f"DEMO-LOCK-{uuid.uuid4().hex[:8].upper()}"
        hold_expires_at = (_datetime.now(UTC) + timedelta(minutes=HOLD_TTL_MINUTES)).isoformat()

        result = BookingResult(
            pnr=pnr,
            offer_lock_id=offer_lock_id,
            hold_expires_at=hold_expires_at,
            idempotency_key=idempotency_key,
            audit_id=uuid.uuid4(),
        )
        record = _HoldRecord(offer_id=offer_id, idempotency_key=idempotency_key, result=result)
        self._holds[pnr] = record
        self._idempotency_index[idempotency_key] = pnr
        return result

    async def cancel(self, booking_ref: str) -> CancellationResult:
        """Release a hold. Idempotent; cross-instance safe.

        Same-instance path: checked against _holds (exact).
        Cross-instance fallback: cryptographically verifies the PNR token —
        the 8 hex chars encode 4-byte payload + 4-byte HMAC tag, proving this
        system issued the PNR. A never-issued but well-formatted ref fails the
        HMAC check and returns cancelled=False. Garbage refs fail the regex.
        Cancelling an already-cancelled PNR is idempotent (still returns True).
        """
        if booking_ref in self._holds:
            self._holds[booking_ref].cancelled = True
            return CancellationResult(booking_ref=booking_ref, cancelled=True)

        # Cross-instance fallback: PNR was booked on a different Cloud Run instance.
        # Outer gate: format check. Inner gate: HMAC-SHA256 token verification.
        if _DEMO_PNR_RE.match(booking_ref) and _verify_pnr_token(booking_ref[9:]):
            return CancellationResult(booking_ref=booking_ref, cancelled=True)

        return CancellationResult(booking_ref=booking_ref, cancelled=False)

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
