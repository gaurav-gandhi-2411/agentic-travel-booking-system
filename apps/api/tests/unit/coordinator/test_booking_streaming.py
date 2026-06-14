"""Unit tests for booking_streaming module (Phase 3.2-E.1).

Covers stream_book and stream_cancel: happy paths, price-changed gate,
unavailability gate, conflict handling, and provider exception paths.
Real MockBookableProvider is used for idempotency and cancel integration tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from travel_agent.coordinator.booking_streaming import (
    BookingEventType,
    stream_book,
    stream_cancel,
)
from travel_agent.providers.base import (
    BookingConflictError,
    BookingResult,
    CancellationResult,
    RevalidationResult,
)
from travel_agent.providers.mock_bookable.provider import MOCK_CATALOG, MockBookableProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect(gen: object) -> list[dict]:  # type: ignore[type-arg]
    return [e async for e in gen]  # type: ignore[attr-defined]


def _make_reval(
    *,
    offer_id: str = "MOCK-OFFER-001",
    current_price_inr: int = 4_200,
    is_available: bool = True,
    price_changed: bool = False,
    previous_price_inr: int | None = None,
) -> RevalidationResult:
    return RevalidationResult(
        offer_id=offer_id,
        current_price_inr=current_price_inr,
        is_available=is_available,
        price_changed=price_changed,
        previous_price_inr=previous_price_inr,
    )


def _make_book_result(
    *,
    pnr: str = "MOCK-PNR-TESTABCD",
    offer_lock_id: str = "MOCK-LOCK-TEST1234",
    hold_expires_at: str = "2026-12-31T23:59:59+00:00",
    idempotency_key: str = "test-idem-key",
) -> BookingResult:
    return BookingResult(
        pnr=pnr,
        offer_lock_id=offer_lock_id,
        hold_expires_at=hold_expires_at,
        idempotency_key=idempotency_key,
    )


def _mock_provider(
    *,
    reval: RevalidationResult | None = None,
    book_result: BookingResult | None = None,
    reval_exc: Exception | None = None,
    book_exc: Exception | None = None,
    cancel_result: CancellationResult | None = None,
    cancel_exc: Exception | None = None,
) -> MagicMock:
    """Build a mock BookableInventoryProvider with configurable behaviour."""
    provider = MagicMock()

    if reval_exc is not None:
        provider.revalidate = AsyncMock(side_effect=reval_exc)
    else:
        provider.revalidate = AsyncMock(return_value=reval or _make_reval())

    if book_exc is not None:
        provider.book = AsyncMock(side_effect=book_exc)
    else:
        provider.book = AsyncMock(return_value=book_result or _make_book_result())

    if cancel_exc is not None:
        provider.cancel = AsyncMock(side_effect=cancel_exc)
    else:
        provider.cancel = AsyncMock(
            return_value=cancel_result or CancellationResult(booking_ref="REF-001", cancelled=True)
        )

    return provider


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# ---------------------------------------------------------------------------
# stream_book — happy path
# ---------------------------------------------------------------------------


async def test_stream_book_happy_path_event_sequence() -> None:
    """Happy-path stream_book emits revalidating → priced → confirmed; no error."""
    provider = _mock_provider()
    events = await _collect(stream_book("MOCK-OFFER-001", "key-001", provider))
    types = _types(events)

    assert BookingEventType.BOOKING_REVALIDATING in types
    assert BookingEventType.BOOKING_PRICED in types
    assert BookingEventType.BOOKING_CONFIRMED in types
    assert BookingEventType.BOOKING_ERROR not in types

    # ordering check
    assert types.index(BookingEventType.BOOKING_REVALIDATING) < types.index(
        BookingEventType.BOOKING_PRICED
    )
    assert types.index(BookingEventType.BOOKING_PRICED) < types.index(
        BookingEventType.BOOKING_CONFIRMED
    )


async def test_stream_book_confirmed_sandbox_flag() -> None:
    """booking_confirmed event always carries sandbox=True."""
    provider = _mock_provider()
    events = await _collect(stream_book("MOCK-OFFER-001", "key-002", provider))
    confirmed = next(e for e in events if e["type"] == BookingEventType.BOOKING_CONFIRMED)
    assert confirmed["sandbox"] is True


async def test_stream_book_priced_fields() -> None:
    """booking_priced carries the required fields from RevalidationResult plus sandbox=True."""
    reval = _make_reval(
        offer_id="MOCK-OFFER-001",
        current_price_inr=4_200,
        is_available=True,
        price_changed=False,
    )
    provider = _mock_provider(reval=reval)
    events = await _collect(stream_book("MOCK-OFFER-001", "key-003", provider))
    priced = next(e for e in events if e["type"] == BookingEventType.BOOKING_PRICED)

    assert priced["offer_id"] == "MOCK-OFFER-001"
    assert priced["current_price_inr"] == 4_200
    assert priced["is_available"] is True
    assert priced["price_changed"] is False
    assert priced["sandbox"] is True


# ---------------------------------------------------------------------------
# stream_book — price-changed gate
# ---------------------------------------------------------------------------


async def test_stream_book_price_changed_stops_no_book_call() -> None:
    """If price_changed=True, stream stops after booking_priced; book() is never called."""
    reval = _make_reval(price_changed=True, previous_price_inr=4_000, current_price_inr=4_500)
    provider = _mock_provider(reval=reval)
    events = await _collect(stream_book("MOCK-OFFER-001", "key-004", provider))
    types = _types(events)

    assert BookingEventType.BOOKING_PRICED in types
    assert BookingEventType.BOOKING_CONFIRMED not in types
    provider.book.assert_not_awaited()


async def test_stream_book_price_changed_includes_previous_price() -> None:
    """When price_changed=True, booking_priced includes previous_price_inr."""
    reval = _make_reval(price_changed=True, previous_price_inr=4_000, current_price_inr=4_500)
    provider = _mock_provider(reval=reval)
    events = await _collect(stream_book("MOCK-OFFER-001", "key-005", provider))
    priced = next(e for e in events if e["type"] == BookingEventType.BOOKING_PRICED)

    assert priced["previous_price_inr"] == 4_000


# ---------------------------------------------------------------------------
# stream_book — unavailable gate
# ---------------------------------------------------------------------------


async def test_stream_book_unavailable_emits_error_no_book() -> None:
    """When is_available=False, booking_error code=unavailable is emitted; book() not called."""
    reval = _make_reval(is_available=False)
    provider = _mock_provider(reval=reval)
    events = await _collect(stream_book("MOCK-OFFER-001", "key-006", provider))
    types = _types(events)

    assert BookingEventType.BOOKING_ERROR in types
    error = next(e for e in events if e["type"] == BookingEventType.BOOKING_ERROR)
    assert error["code"] == "unavailable"
    provider.book.assert_not_awaited()


# ---------------------------------------------------------------------------
# stream_book — error paths
# ---------------------------------------------------------------------------


async def test_stream_book_conflict_emits_conflict_error() -> None:
    """BookingConflictError from book() is mapped to booking_error code=conflict."""
    provider = _mock_provider(book_exc=BookingConflictError("already bound to offer-001"))
    events = await _collect(stream_book("MOCK-OFFER-002", "conflict-key", provider))
    types = _types(events)

    assert BookingEventType.BOOKING_CONFIRMED not in types
    assert BookingEventType.BOOKING_ERROR in types
    error = next(e for e in events if e["type"] == BookingEventType.BOOKING_ERROR)
    assert error["code"] == "conflict"


async def test_stream_book_revalidate_exception_emits_provider_error() -> None:
    """Unhandled exception from revalidate() maps to booking_error code=provider_error."""
    provider = _mock_provider(reval_exc=RuntimeError("network timeout"))
    events = await _collect(stream_book("MOCK-OFFER-001", "key-007", provider))
    types = _types(events)

    assert types[0] == BookingEventType.BOOKING_REVALIDATING
    assert BookingEventType.BOOKING_ERROR in types
    error = next(e for e in events if e["type"] == BookingEventType.BOOKING_ERROR)
    assert error["code"] == "provider_error"


# ---------------------------------------------------------------------------
# stream_book — idempotency (real MockBookableProvider)
# ---------------------------------------------------------------------------


async def test_stream_book_idempotency_same_key_same_offer() -> None:
    """Two calls with the same key+offer on a real provider both confirm with the same PNR."""
    provider = MockBookableProvider()
    offer_id = MOCK_CATALOG[0].offer_id

    events1 = await _collect(stream_book(offer_id, "idem-key-A", provider))
    events2 = await _collect(stream_book(offer_id, "idem-key-A", provider))

    confirmed1 = next(e for e in events1 if e["type"] == BookingEventType.BOOKING_CONFIRMED)
    confirmed2 = next(e for e in events2 if e["type"] == BookingEventType.BOOKING_CONFIRMED)

    assert confirmed1["pnr"] == confirmed2["pnr"]


async def test_stream_book_idempotency_same_key_different_offer_conflict() -> None:
    """Reusing an idempotency key for a different offer yields booking_error code=conflict."""
    assert len(MOCK_CATALOG) >= 2, "Need at least 2 offers in MOCK_CATALOG"
    provider = MockBookableProvider()

    offer1 = MOCK_CATALOG[0].offer_id
    offer2 = MOCK_CATALOG[1].offer_id

    events1 = await _collect(stream_book(offer1, "shared-key-B", provider))
    assert any(e["type"] == BookingEventType.BOOKING_CONFIRMED for e in events1)

    events2 = await _collect(stream_book(offer2, "shared-key-B", provider))
    types2 = _types(events2)
    assert BookingEventType.BOOKING_CONFIRMED not in types2
    assert BookingEventType.BOOKING_ERROR in types2
    error = next(e for e in events2 if e["type"] == BookingEventType.BOOKING_ERROR)
    assert error["code"] == "conflict"


# ---------------------------------------------------------------------------
# stream_cancel — happy path (real MockBookableProvider)
# ---------------------------------------------------------------------------


async def test_stream_cancel_happy_path() -> None:
    """Book via real provider, then cancel — emits booking_cancelled with cancelled=True."""
    provider = MockBookableProvider()
    offer_id = MOCK_CATALOG[0].offer_id

    book_events = await _collect(stream_book(offer_id, "cancel-test-key-1", provider))
    confirmed = next(e for e in book_events if e["type"] == BookingEventType.BOOKING_CONFIRMED)
    pnr = confirmed["pnr"]

    cancel_events = await _collect(stream_cancel(pnr, provider))
    assert len(cancel_events) == 1
    event = cancel_events[0]
    assert event["type"] == BookingEventType.BOOKING_CANCELLED
    assert event["cancelled"] is True
    assert event["sandbox"] is True


# ---------------------------------------------------------------------------
# stream_cancel — error paths
# ---------------------------------------------------------------------------


async def test_stream_cancel_unknown_ref_emits_not_found() -> None:
    """Cancelling an unknown booking_ref emits booking_error code=not_found."""
    provider = MockBookableProvider()

    events = await _collect(stream_cancel("NONEXISTENT-PNR", provider))
    assert len(events) == 1
    error = events[0]
    assert error["type"] == BookingEventType.BOOKING_ERROR
    assert error["code"] == "not_found"
    assert "not found" in error["message"].lower()


async def test_stream_cancel_provider_exception_emits_provider_error() -> None:
    """Unhandled exception from provider.cancel() maps to booking_error code=provider_error."""
    provider = _mock_provider(cancel_exc=RuntimeError("provider boom"))
    events = await _collect(stream_cancel("ANY-PNR", provider))

    assert len(events) == 1
    error = events[0]
    assert error["type"] == BookingEventType.BOOKING_ERROR
    assert error["code"] == "provider_error"
