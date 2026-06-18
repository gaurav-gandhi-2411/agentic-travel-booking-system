"""DemoProvider — unified search + bookable provider for the closed-loop demo.

Implements BOTH InventoryProvider (search: flights + hotels) and
BookableInventoryProvider (revalidate / book / cancel) over ONE in-memory
catalog with ONE coherent offer-ID namespace.

A FlightOption.id returned by get_flights() is accepted directly by
revalidate() / book() — no translation required.

ID scheme: DEMO-FLT-NNN-YYYY-MM-DD  (search returns window-qualified IDs)
           DEMO-HTL-NNN-YYYY-MM-DD
Booking/cancel use the full window-qualified ID; internally _base_offer_id()
strips the date suffix to look up the catalog.

PRICE-CHANGED TRIGGER (stateful):
  DEMO-FLT-005 first revalidate → price_changed=True (5,100→7,200).
  Subsequent revalidates of the same base offer → price_changed=False at 7,200.
  This means: first /book attempt stops at the confirm step; second /book
  proceeds to a successful DEMO-PNR booking. Test BOTH legs.

THIS IS A SANDBOX MOCK. No real inventory, no real PNR, no payments.
"""

from __future__ import annotations

import dataclasses
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

# ── catalog entries ────────────────────────────────────────────────────────────


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

# Indexed by base offer ID (e.g. "DEMO-FLT-001")
_FLIGHT_INDEX: dict[str, _DemoFlight] = {f.offer_id: f for f in _FLIGHT_CATALOG}
_HOTEL_INDEX: dict[str, _DemoHotel] = {h.offer_id: h for h in _HOTEL_CATALOG}

PRICE_CHANGE_OFFER_ID = "DEMO-FLT-005"
PRICE_CHANGE_ORIGINAL_PRICE = 4_600
PRICE_CHANGE_NEW_PRICE = 7_200

# DEMO offer IDs have the form DEMO-{TYPE}-{NNN}; the base ID is always 3 dash-separated parts.
_OFFER_ID_BASE_PARTS: int = 3


def _base_offer_id(offer_id: str) -> str:
    """Extract DEMO-FLT-NNN (or DEMO-HTL-NNN) from a window-qualified ID.

    "DEMO-FLT-001-2024-03-15" -> "DEMO-FLT-001"
    "DEMO-FLT-001" -> "DEMO-FLT-001"  (already bare, e.g. in tests)
    """
    parts = offer_id.split("-")
    if len(parts) >= _OFFER_ID_BASE_PARTS:
        return "-".join(parts[:_OFFER_ID_BASE_PARTS])
    return offer_id


# ── internal hold state ────────────────────────────────────────────────────────


@dataclasses.dataclass
class _HoldRecord:
    offer_id: str  # window-qualified ID as passed to book()
    idempotency_key: str
    result: BookingResult
    cancelled: bool = False


# ── provider ──────────────────────────────────────────────────────────────────


class DemoProvider:
    """Unified search + bookable provider for the closed-loop demo.

    THIS IS A SANDBOX MOCK. No real inventory, payments, or PNRs.
    """

    def __init__(self) -> None:
        self._holds: dict[str, _HoldRecord] = {}  # pnr → HoldRecord
        self._idempotency_index: dict[str, str] = {}  # idempotency_key → pnr
        self._price_changed_shown: set[str] = set()  # base offer IDs shown price change

    # ── InventoryProvider ──────────────────────────────────────────────────

    async def close(self) -> None:
        self._holds.clear()
        self._idempotency_index.clear()
        self._price_changed_shown.clear()

    # ── Search (same interface as SyntheticProvider) ───────────────────────

    def get_flights(
        self,
        origin: str,
        destination: str,
        window: Window,
        *,
        trip_type: TripType = TripType.ROUND_TRIP,
        trip_duration_days: int = 7,
    ) -> list[FlightOption]:
        """Return demo flights matching origin/destination for the given window."""
        is_one_way = trip_type == TripType.ONE_WAY
        results: list[FlightOption] = []
        for tmpl in _FLIGHT_CATALOG:
            if tmpl.origin_iata != origin or tmpl.destination_iata != destination:
                continue
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

            # Window-qualified ID: DEMO-FLT-001-2024-03-15
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

        DEMO-FLT-005 (or its window-qualified form) triggers the price-changed
        path on first call, then settles at the new price for subsequent calls.
        """
        base = _base_offer_id(offer_id)
        self._require_offer(base)

        if base == PRICE_CHANGE_OFFER_ID and base not in self._price_changed_shown:
            self._price_changed_shown.add(base)
            return RevalidationResult(
                offer_id=offer_id,
                current_price_inr=PRICE_CHANGE_NEW_PRICE,
                is_available=True,
                price_changed=True,
                previous_price_inr=PRICE_CHANGE_ORIGINAL_PRICE,
            )

        current_price = (
            PRICE_CHANGE_NEW_PRICE if base == PRICE_CHANGE_OFFER_ID else self._offer_price(base)
        )
        return RevalidationResult(
            offer_id=offer_id,
            current_price_inr=current_price,
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
        if base_id not in _FLIGHT_INDEX and base_id not in _HOTEL_INDEX:
            msg = f"[DEMO] Unknown offer_id: {base_id!r}"
            raise InventoryClientError(msg)

    def _offer_price(self, base_id: str) -> int:
        if base_id in _FLIGHT_INDEX:
            return _FLIGHT_INDEX[base_id].price_inr
        return _HOTEL_INDEX[base_id].price_per_night_inr
