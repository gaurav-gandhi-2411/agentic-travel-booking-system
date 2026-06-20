"""Booking-phase SSE coordinator.

stream_book:   offer_id + idempotency_key + provider → async generator
stream_cancel: booking_ref + provider → async generator

Price-changed gate (user-confirmed requirement):
  If revalidation returns price_changed=True, emit booking_priced and STOP.
  The caller must issue a fresh /book (new idempotency_key acceptable) to
  confirm at the new price. Silent auto-confirm at a new price is forbidden.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import StrEnum

import structlog

from travel_agent.agents.booking import BookingAgent
from travel_agent.coordinator.state import RequestState
from travel_agent.providers.base import (
    BookableInventoryProvider,
    BookingConflictError,
)

log = structlog.get_logger(__name__)


class BookingEventType(StrEnum):
    BOOKING_REVALIDATING = "booking_revalidating"
    BOOKING_PRICED = "booking_priced"
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_CANCELLED = "booking_cancelled"
    BOOKING_ERROR = "booking_error"


async def stream_book(
    offer_id: str,
    idempotency_key: str,
    provider: BookableInventoryProvider,
    accept_price_change: bool = False,
) -> AsyncGenerator[dict[str, object], None]:
    """Async generator: revalidate → (price gate) → book → confirm.

    Emits booking_revalidating, then booking_priced. If price_changed=True and
    accept_price_change=False, stops here — caller must re-issue /book with
    accept_price_change=True (the price-confirm step). If price_changed=False, or
    if the caller has already accepted the new price via accept_price_change=True,
    proceeds to book() and emits booking_confirmed. Emits booking_error on failure.

    The provider's revalidate() is stateless for price-change triggers (always
    returns price_changed=True for the trigger offer). The halt/proceed decision
    is made entirely from accept_price_change — no shared or per-instance state.
    """
    log.info("booking_start", offer_id=offer_id, accept_price_change=accept_price_change)
    yield {"type": BookingEventType.BOOKING_REVALIDATING}

    try:
        reval = await provider.revalidate(offer_id)
    except Exception as exc:
        log.warning("booking_error_emitted", offer_id=offer_id, code="provider_error", error=str(exc))
        yield {
            "type": BookingEventType.BOOKING_ERROR,
            "message": str(exc),
            "code": "provider_error",
        }
        return

    priced: dict[str, object] = {
        "type": BookingEventType.BOOKING_PRICED,
        "offer_id": reval.offer_id,
        "current_price_inr": reval.current_price_inr,
        "is_available": reval.is_available,
        "price_changed": reval.price_changed,
        "sandbox": True,
    }
    if reval.previous_price_inr is not None:
        priced["previous_price_inr"] = reval.previous_price_inr

    if not reval.is_available:
        yield priced
        log.warning("booking_error_emitted", offer_id=offer_id, code="unavailable")
        yield {
            "type": BookingEventType.BOOKING_ERROR,
            "message": "Offer is no longer available.",
            "code": "unavailable",
        }
        return

    if reval.price_changed and not accept_price_change:
        # Price changed — emit priced event and stop. Caller must re-issue /book
        # with accept_price_change=True to proceed at the new price.
        log.info(
            "booking_price_change_halt",
            offer_id=offer_id,
            previous_price=reval.previous_price_inr,
            new_price=reval.current_price_inr,
        )
        yield priced
        return

    # price_changed=False (clean offer), OR caller has explicitly accepted the
    # new price — proceed to book. priced event is still emitted (informational).
    yield priced

    agent = BookingAgent(provider)
    try:
        state = await agent.run(RequestState(), offer_id, idempotency_key)
    except BookingConflictError as exc:
        log.warning("booking_error_emitted", offer_id=offer_id, code="conflict", error=str(exc))
        yield {
            "type": BookingEventType.BOOKING_ERROR,
            "message": str(exc),
            "code": "conflict",
        }
        return
    except Exception as exc:
        log.warning("booking_error_emitted", offer_id=offer_id, code="provider_error", error=str(exc))
        yield {
            "type": BookingEventType.BOOKING_ERROR,
            "message": str(exc),
            "code": "provider_error",
        }
        return

    b = state.booking
    log.info(
        "booking_confirmed",
        offer_id=offer_id,
        pnr=b.pnr,
        audit_id=str(b.audit_id) if b.audit_id is not None else None,
        confirmed_price_inr=reval.current_price_inr,
    )
    yield {
        "type": BookingEventType.BOOKING_CONFIRMED,
        "pnr": b.pnr,
        "offer_lock_id": b.offer_lock_id,
        "hold_expires_at": b.hold_expires_at,
        "idempotency_key": b.idempotency_key,
        "audit_id": str(b.audit_id) if b.audit_id is not None else None,
        # confirmed_price_inr: the price the user saw and accepted before booking.
        "confirmed_price_inr": reval.current_price_inr,
        "sandbox": True,
    }


async def stream_cancel(
    booking_ref: str,
    provider: BookableInventoryProvider,
) -> AsyncGenerator[dict[str, object], None]:
    """Async generator: cancel a hold by booking_ref.

    If the booking_ref is unknown or already cancelled, the provider returns
    cancelled=False and we emit booking_error {code: "not_found"}.
    """
    log.info("cancel_start", booking_ref=booking_ref)
    try:
        result = await provider.cancel(booking_ref)
    except Exception as exc:
        log.warning("booking_error_emitted", booking_ref=booking_ref, code="provider_error", error=str(exc))
        yield {
            "type": BookingEventType.BOOKING_ERROR,
            "message": str(exc),
            "code": "provider_error",
        }
        return

    if not result.cancelled:
        log.warning("booking_error_emitted", booking_ref=booking_ref, code="not_found")
        yield {
            "type": BookingEventType.BOOKING_ERROR,
            "message": f"Booking ref {booking_ref!r} not found or already cancelled.",
            "code": "not_found",
        }
        return

    log.info("cancel_result", booking_ref=booking_ref, cancelled=True)
    yield {
        "type": BookingEventType.BOOKING_CANCELLED,
        "booking_ref": result.booking_ref,
        "cancelled": True,
        "sandbox": True,
    }
