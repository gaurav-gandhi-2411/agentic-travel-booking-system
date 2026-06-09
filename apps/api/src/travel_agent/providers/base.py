"""Shared InventoryProvider lifecycle contract.

Both AviasalesAdapter (flights) and HotellookAdapter (hotels) satisfy this
protocol structurally — no inheritance required.

Normalization position: this contract is agnostic. It defines lifecycle
(close) and a shared error hierarchy only. Return types for search methods
are left to each vertical adapter; the existing flight-normalization split
(raw dicts in adapter, typed FlightOption in FlightHunterAgent) is preserved.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol, runtime_checkable
from uuid import UUID


class InventoryProviderError(Exception):
    """Base exception for all inventory provider errors."""


class InventoryRateLimitError(InventoryProviderError):
    """Provider returned HTTP 429 or signaled rate limiting."""


class InventoryServerError(InventoryProviderError):
    """Provider returned HTTP 5xx."""


class InventoryClientError(InventoryProviderError):
    """Provider returned HTTP 4xx (non-429)."""


class BookingConflictError(InventoryProviderError):
    """Same idempotency_key was reused with a different offer_id."""


@dataclasses.dataclass(frozen=True)
class RevalidationResult:
    """Price and availability snapshot returned by revalidate()."""

    offer_id: str
    current_price_inr: int
    is_available: bool
    price_changed: bool
    previous_price_inr: int | None = None


@dataclasses.dataclass(frozen=True)
class BookingResult:
    """Provider-level booking record. Fields mirror coordinator.state.BookingStatus."""

    pnr: str                      # → BookingStatus.pnr
    offer_lock_id: str            # → BookingStatus.offer_lock_id
    hold_expires_at: str          # ISO 8601 → BookingStatus.hold_expires_at
    idempotency_key: str          # → BookingStatus.idempotency_key
    audit_id: UUID | None = None  # → BookingStatus.audit_id


@dataclasses.dataclass(frozen=True)
class CancellationResult:
    """Confirmation of a hold/booking cancellation."""

    booking_ref: str
    cancelled: bool


@runtime_checkable
class InventoryProvider(Protocol):
    """Lifecycle contract shared by all inventory adapters.

    Defines only async cleanup. Search method signatures and return types
    are vertical-specific and NOT part of this base protocol.
    """

    async def close(self) -> None:
        """Release underlying HTTP client / connection pool."""
        ...


@runtime_checkable
class BookableInventoryProvider(InventoryProvider, Protocol):
    """Extends InventoryProvider with the bookable lifecycle.

    AviasalesAdapter conforms to InventoryProvider but NOT this protocol.
    Only adapters backed by a real or mock bookable distributor implement this.
    The contract a BYO-inventory licensee or future real-distributor adapter
    (TBO/Amadeus) must satisfy.
    """

    async def revalidate(self, offer_id: str) -> RevalidationResult:
        """Re-confirm current price and availability for a selected offer."""
        ...

    async def book(self, offer_id: str, idempotency_key: str) -> BookingResult:
        """Create a hold/booking. Idempotent on (offer_id, idempotency_key).

        Idempotency contract:
          - Same idempotency_key + same offer_id → return the original BookingResult.
          - Same idempotency_key + different offer_id → raise BookingConflictError.
            The key is bound to the first offer at call time; reuse with different
            intent is a programming error and must not silently create a second booking.
        """
        ...

    async def cancel(self, booking_ref: str) -> CancellationResult:
        """Release a hold or cancel a booking by its booking_ref (pnr)."""
        ...
