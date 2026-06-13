"""Unit tests for book.py SSE generator helpers (Phase 3.2-E.1).

Tests _sse_book_generator and _sse_cancel_generator directly — no HTTP client
needed. Follows the same pattern as test_refine.py which tests _refine_generator
directly.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from travel_agent.api.routes.book import (
    BookRequest,
    CancelRequest,
    _sse_book_generator,
    _sse_cancel_generator,
)
from travel_agent.coordinator.booking_streaming import (
    BookingEventType,
    stream_book,
)
from travel_agent.providers.mock_bookable.provider import MOCK_CATALOG, MockBookableProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect(gen: object) -> list[dict]:  # type: ignore[type-arg]
    events: list[dict] = []
    async for line in gen:  # type: ignore[attr-defined]
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ---------------------------------------------------------------------------
# _sse_book_generator — not_bookable guard
# ---------------------------------------------------------------------------


async def test_book_aviasales_tenant_returns_not_bookable() -> None:
    """An 'aviasales' inventory_adapter slug returns a single not_bookable error event."""
    body = BookRequest(offer_id="MOCK-OFFER-001", idempotency_key="idem-test-av-1")
    events = await _collect(_sse_book_generator(body, "aviasales"))

    assert len(events) == 1
    event = events[0]
    assert event["type"] == BookingEventType.BOOKING_ERROR
    assert event["code"] == "not_bookable"
    assert "aviasales" in event["message"]


async def test_book_mock_tenant_returns_confirmed() -> None:
    """A mock_bookable tenant with a fresh provider instance returns booking_confirmed."""
    fresh_provider = MockBookableProvider()
    body = BookRequest(offer_id="MOCK-OFFER-001", idempotency_key="idem-mock-001")

    with patch("travel_agent.api.routes.book.get_bookable_provider", return_value=fresh_provider):
        events = await _collect(_sse_book_generator(body, "mock_bookable"))

    types = [e["type"] for e in events]
    assert BookingEventType.BOOKING_CONFIRMED in types


# ---------------------------------------------------------------------------
# _sse_cancel_generator — not_bookable guard
# ---------------------------------------------------------------------------


async def test_cancel_aviasales_tenant_returns_not_bookable() -> None:
    """An 'aviasales' cancel request returns a single not_bookable error event."""
    body = CancelRequest(booking_ref="NONEXISTENT-PNR")
    events = await _collect(_sse_cancel_generator(body, "aviasales"))

    assert len(events) == 1
    event = events[0]
    assert event["type"] == BookingEventType.BOOKING_ERROR
    assert event["code"] == "not_bookable"


# ---------------------------------------------------------------------------
# _sse_cancel_generator — not_found path
# ---------------------------------------------------------------------------


async def test_cancel_unknown_ref_returns_not_found() -> None:
    """Cancelling an unknown PNR via a fresh provider returns booking_error code=not_found."""
    fresh_provider = MockBookableProvider()
    body = CancelRequest(booking_ref="TOTALLY-UNKNOWN-REF")

    with patch("travel_agent.api.routes.book.get_bookable_provider", return_value=fresh_provider):
        events = await _collect(_sse_cancel_generator(body, "mock_bookable"))

    assert len(events) == 1
    event = events[0]
    assert event["type"] == BookingEventType.BOOKING_ERROR
    assert event["code"] == "not_found"


# ---------------------------------------------------------------------------
# _sse_cancel_generator — happy path
# ---------------------------------------------------------------------------


async def test_cancel_happy_path_returns_cancelled() -> None:
    """Book via stream_book, then cancel via _sse_cancel_generator → booking_cancelled."""
    fresh_provider = MockBookableProvider()
    offer_id = MOCK_CATALOG[0].offer_id

    # Book first to get a real PNR
    book_events = [e async for e in stream_book(offer_id, "cancel-endpoint-key-1", fresh_provider)]
    confirmed = next(e for e in book_events if e["type"] == BookingEventType.BOOKING_CONFIRMED)
    pnr = confirmed["pnr"]

    body = CancelRequest(booking_ref=pnr)
    with patch("travel_agent.api.routes.book.get_bookable_provider", return_value=fresh_provider):
        events = await _collect(_sse_cancel_generator(body, "mock_bookable"))

    assert len(events) == 1
    event = events[0]
    assert event["type"] == BookingEventType.BOOKING_CANCELLED
    assert event["cancelled"] is True


# ---------------------------------------------------------------------------
# _sse_book_generator — idempotency (same provider instance across calls)
# ---------------------------------------------------------------------------


async def test_book_idempotency_same_key_same_offer_same_pnr() -> None:
    """Two calls with same idempotency_key+offer_id on the same provider return same PNR."""
    fresh_provider = MockBookableProvider()
    offer_id = MOCK_CATALOG[0].offer_id
    body = BookRequest(offer_id=offer_id, idempotency_key="idem-same-1")

    with patch("travel_agent.api.routes.book.get_bookable_provider", return_value=fresh_provider):
        events1 = await _collect(_sse_book_generator(body, "mock_bookable"))
        events2 = await _collect(_sse_book_generator(body, "mock_bookable"))

    confirmed1 = next(e for e in events1 if e["type"] == BookingEventType.BOOKING_CONFIRMED)
    confirmed2 = next(e for e in events2 if e["type"] == BookingEventType.BOOKING_CONFIRMED)
    assert confirmed1["pnr"] == confirmed2["pnr"]


async def test_book_idempotency_same_key_different_offer_conflict() -> None:
    """Reusing an idempotency key for a different offer via the same provider → conflict error."""
    assert len(MOCK_CATALOG) >= 2, "Need at least 2 offers in MOCK_CATALOG"
    fresh_provider = MockBookableProvider()

    body1 = BookRequest(offer_id=MOCK_CATALOG[0].offer_id, idempotency_key="shared-endpoint-key-X")
    body2 = BookRequest(offer_id=MOCK_CATALOG[1].offer_id, idempotency_key="shared-endpoint-key-X")

    with patch("travel_agent.api.routes.book.get_bookable_provider", return_value=fresh_provider):
        events1 = await _collect(_sse_book_generator(body1, "mock_bookable"))
        assert any(e["type"] == BookingEventType.BOOKING_CONFIRMED for e in events1)

        events2 = await _collect(_sse_book_generator(body2, "mock_bookable"))

    types2 = [e["type"] for e in events2]
    assert BookingEventType.BOOKING_CONFIRMED not in types2
    assert BookingEventType.BOOKING_ERROR in types2
    error = next(e for e in events2 if e["type"] == BookingEventType.BOOKING_ERROR)
    assert error["code"] == "conflict"
