"""MockBookableProvider — deterministic reference implementation.

THIS IS A MOCK. It performs no real booking, issues no real PNRs, and holds
no real inventory. It exists solely to exercise the BookableInventoryProvider
contract and prove the capability-segregation seam. Do not use in production.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, timedelta
from datetime import datetime as _datetime

from travel_agent.providers.base import (
    BookableInventoryProvider,  # noqa: F401 — Protocol conformance marker
    BookingConflictError,
    BookingResult,
    CancellationResult,
    InventoryClientError,
    RevalidationResult,
)

HOLD_TTL_MINUTES: int = 15  # mock hold duration; a real adapter may differ


@dataclasses.dataclass(frozen=True)
class MockOffer:
    """A single deterministic mock offer in the catalog."""

    offer_id: str
    description: str
    price_inr: int
    is_available: bool = True


# Deterministic mock catalog — fixed IDs, fixed prices, no RNG.
MOCK_CATALOG: tuple[MockOffer, ...] = (
    MockOffer("MOCK-OFFER-001", "DEL→BOM Economy 08:00", 4_200),
    MockOffer("MOCK-OFFER-002", "DEL→BOM Business 10:00", 12_500),
    MockOffer("MOCK-OFFER-003", "DEL→GOI Economy 14:00", 6_800),
)

_CATALOG_INDEX: dict[str, MockOffer] = {o.offer_id: o for o in MOCK_CATALOG}


@dataclasses.dataclass
class _HoldRecord:
    """Internal hold state — not exposed outside this module."""

    offer_id: str
    idempotency_key: str
    result: BookingResult
    cancelled: bool = False


class MockBookableProvider:
    """Deterministic reference implementation of BookableInventoryProvider.

    THIS IS A MOCK. No real inventory, no real bookings, no payments, no PNRs.
    All state is in-memory and scoped to the lifetime of this instance.
    """

    def __init__(self) -> None:
        # booking_ref (pnr) → HoldRecord
        self._holds: dict[str, _HoldRecord] = {}
        # idempotency_key → booking_ref (pnr)
        self._idempotency_index: dict[str, str] = {}

    # ── InventoryProvider ──────────────────────────────────────────────────

    async def close(self) -> None:
        """Release all in-memory state (satisfies InventoryProvider.close)."""
        self._holds.clear()
        self._idempotency_index.clear()

    # ── Mock search (not part of any base Protocol) ────────────────────────

    def search(self) -> list[MockOffer]:
        """Return the full deterministic mock catalog."""
        return list(MOCK_CATALOG)

    # ── BookableInventoryProvider ──────────────────────────────────────────

    async def revalidate(self, offer_id: str) -> RevalidationResult:
        """Re-confirm price and availability. Always returns current mock price."""
        offer = self._require_offer(offer_id)
        return RevalidationResult(
            offer_id=offer_id,
            current_price_inr=offer.price_inr,
            is_available=offer.is_available,
            price_changed=False,
        )

    async def book(self, offer_id: str, idempotency_key: str) -> BookingResult:
        """Create a mock hold. Idempotent; raises BookingConflictError on key reuse."""
        if idempotency_key in self._idempotency_index:
            booking_ref = self._idempotency_index[idempotency_key]
            record = self._holds[booking_ref]
            if record.offer_id != offer_id:
                msg = (
                    f"Idempotency key {idempotency_key!r} is already bound to "
                    f"offer {record.offer_id!r}; cannot reuse for {offer_id!r}."
                )
                raise BookingConflictError(msg)
            return record.result

        self._require_offer(offer_id)  # validate existence before allocating IDs

        pnr = f"MOCK-PNR-{uuid.uuid4().hex[:8].upper()}"
        offer_lock_id = f"MOCK-LOCK-{uuid.uuid4().hex[:8].upper()}"
        hold_expires_at = (
            _datetime.now(UTC) + timedelta(minutes=HOLD_TTL_MINUTES)
        ).isoformat()

        result = BookingResult(
            pnr=pnr,
            offer_lock_id=offer_lock_id,
            hold_expires_at=hold_expires_at,
            idempotency_key=idempotency_key,
        )
        record = _HoldRecord(
            offer_id=offer_id,
            idempotency_key=idempotency_key,
            result=result,
        )
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

    def _require_offer(self, offer_id: str) -> MockOffer:
        try:
            return _CATALOG_INDEX[offer_id]
        except KeyError as exc:
            msg = f"[MOCK] Unknown offer_id: {offer_id!r}"
            raise InventoryClientError(msg) from exc
