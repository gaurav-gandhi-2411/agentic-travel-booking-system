from __future__ import annotations

"""Tests for MockBookableProvider and capability-segregation seam (Phase 3.2-C.1)."""

from datetime import datetime, timezone

import pytest

from travel_agent.providers.aviasales.adapter import AviasalesAdapter
from travel_agent.providers.base import (
    BookableInventoryProvider,
    BookingConflictError,
    InventoryClientError,
    InventoryProvider,
)
from travel_agent.providers.mock_bookable.provider import MOCK_CATALOG, MockBookableProvider

# ---------------------------------------------------------------------------
# Group 1: Capability Segregation (conformance)
# ---------------------------------------------------------------------------


def test_aviasales_is_inventory_provider_not_bookable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AviasalesAdapter satisfies InventoryProvider but NOT BookableInventoryProvider."""
    monkeypatch.setenv("AVIASALES_API_KEY", "test-key-123")
    adapter = AviasalesAdapter()
    assert isinstance(adapter, InventoryProvider) is True
    assert isinstance(adapter, BookableInventoryProvider) is False


def test_mock_is_both_protocols() -> None:
    """MockBookableProvider satisfies both InventoryProvider and BookableInventoryProvider."""
    mock = MockBookableProvider()
    assert isinstance(mock, InventoryProvider) is True
    assert isinstance(mock, BookableInventoryProvider) is True


# ---------------------------------------------------------------------------
# Group 2: Full bookable lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_search_revalidate_book_cancel() -> None:
    """Happy-path lifecycle: search → revalidate → book → idempotent retry → cancel."""
    mock = MockBookableProvider()

    # 1. search() returns non-empty list
    offers = mock.search()
    assert len(offers) > 0
    offer = offers[0]

    # 2. revalidate
    rv = await mock.revalidate(offer.offer_id)
    assert rv.offer_id == offer.offer_id
    assert rv.is_available is True
    assert rv.current_price_inr == offer.price_inr
    assert rv.price_changed is False

    # 3. book
    result = await mock.book(offer.offer_id, idempotency_key="idem-001")
    assert result.pnr.startswith("MOCK-PNR-")
    assert result.offer_lock_id.startswith("MOCK-LOCK-")
    assert result.idempotency_key == "idem-001"

    # 4. hold_expires_at is a future aware datetime
    parsed_expires = datetime.fromisoformat(result.hold_expires_at)
    assert parsed_expires > datetime.now(timezone.utc)

    # 5. Idempotent retry: same offer_id + same key → same PNR and lock ID
    result2 = await mock.book(offer.offer_id, idempotency_key="idem-001")
    assert result2.pnr == result.pnr
    assert result2.offer_lock_id == result.offer_lock_id

    # 6. cancel
    cancel_result = await mock.cancel(result.pnr)
    assert cancel_result.cancelled is True
    assert cancel_result.booking_ref == result.pnr


# ---------------------------------------------------------------------------
# Group 3: Idempotency edge — SAME KEY + DIFFERENT OFFER
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_conflict_different_offer() -> None:
    """Reusing an idempotency key for a different offer_id must raise BookingConflictError."""
    assert len(MOCK_CATALOG) >= 2, "Need at least 2 offers in MOCK_CATALOG for this test"

    mock = MockBookableProvider()
    offers = mock.search()

    await mock.book(offers[0].offer_id, idempotency_key="conflict-key")

    with pytest.raises(BookingConflictError, match="already bound"):
        await mock.book(offers[1].offer_id, idempotency_key="conflict-key")


# ---------------------------------------------------------------------------
# Group 4: Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_unknown_ref_returns_false() -> None:
    """cancel() on an unrecognised booking_ref must return cancelled=False."""
    mock = MockBookableProvider()
    cancel_result = await mock.cancel("UNKNOWN-REF-XYZ")
    assert cancel_result.cancelled is False


@pytest.mark.asyncio
async def test_revalidate_unknown_offer_raises() -> None:
    """revalidate() on an unknown offer_id must raise InventoryClientError."""
    mock = MockBookableProvider()
    with pytest.raises(InventoryClientError, match="MOCK-OFFER-DOES-NOT-EXIST"):
        await mock.revalidate("MOCK-OFFER-DOES-NOT-EXIST")


@pytest.mark.asyncio
async def test_book_unknown_offer_raises() -> None:
    """book() on an unknown offer_id must raise InventoryClientError."""
    mock = MockBookableProvider()
    with pytest.raises(InventoryClientError, match="MOCK-OFFER-DOES-NOT-EXIST"):
        await mock.book("MOCK-OFFER-DOES-NOT-EXIST", idempotency_key="some-key")


# ---------------------------------------------------------------------------
# Group 5: close() clears state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_clears_state() -> None:
    """After close(), previously booked PNRs are no longer found by cancel()."""
    mock = MockBookableProvider()
    offers = mock.search()

    result = await mock.book(offers[0].offer_id, idempotency_key="close-test-key")
    pnr = result.pnr

    await mock.close()

    # State cleared — cancel should return cancelled=False (PNR unknown)
    cancel_result = await mock.cancel(pnr)
    assert cancel_result.cancelled is False
