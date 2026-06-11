"""Tests for DemoProvider — search, bookable lifecycle, price-changed trigger.

Groups:
  1. Protocol conformance (2 tests)
  2. Search side: get_flights() (4 tests)
  3. Search→book identity (2 tests)
  4. Full lifecycle (1 test)
  5. Price-changed trigger: BOTH LEGS (3 tests)
  6. Idempotency + conflict (3 tests)
  7. Unknown offer_id (1 test)
  8. close() clears state (1 test)
"""

from __future__ import annotations

from datetime import date

import pytest

from travel_agent.coordinator.state import TripType, Window
from travel_agent.providers.base import (
    BookableInventoryProvider,
    BookingConflictError,
    BookingResult,
    InventoryClientError,
    InventoryProvider,
)
from travel_agent.providers.demo.provider import (
    PRICE_CHANGE_NEW_PRICE,
    PRICE_CHANGE_OFFER_ID,
    PRICE_CHANGE_ORIGINAL_PRICE,
    DemoProvider,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def window() -> Window:
    """Standard test window: 2025-06-15 → 2025-06-22."""
    return Window(start_date=date(2025, 6, 15), end_date=date(2025, 6, 22))


@pytest.fixture
def provider() -> DemoProvider:
    """Fresh DemoProvider instance with empty state."""
    return DemoProvider()


# ---------------------------------------------------------------------------
# Group 1 — Protocol conformance
# ---------------------------------------------------------------------------


def test_demo_provider_is_bookable_inventory_provider() -> None:
    """DemoProvider satisfies BookableInventoryProvider (runtime_checkable)."""
    p = DemoProvider()
    assert isinstance(p, BookableInventoryProvider) is True


def test_demo_provider_is_also_inventory_provider() -> None:
    """DemoProvider satisfies both InventoryProvider and BookableInventoryProvider,
    not just the bare InventoryProvider.
    """
    p = DemoProvider()
    assert isinstance(p, InventoryProvider) is True
    assert isinstance(p, BookableInventoryProvider) is True


# ---------------------------------------------------------------------------
# Group 2 — Search side: get_flights()
# ---------------------------------------------------------------------------


def test_get_flights_returns_demo_offers(provider: DemoProvider, window: Window) -> None:
    """DEL→BOM returns 3 results (DEMO-FLT-001, 002, 005), all with provider='demo'."""
    results = provider.get_flights("DEL", "BOM", window)

    assert len(results) == 3

    for flight in results:
        assert flight.provider == "demo"
        assert flight.id.startswith("DEMO-FLT-")
        assert flight.id.endswith(window.start_date.isoformat())

    returned_ids = {f.id for f in results}
    for base in ("DEMO-FLT-001", "DEMO-FLT-002", "DEMO-FLT-005"):
        assert f"{base}-{window.start_date.isoformat()}" in returned_ids


def test_get_flights_filters_by_route(provider: DemoProvider, window: Window) -> None:
    """BOM→DEL has no catalog entries — must return an empty list."""
    results = provider.get_flights("BOM", "DEL", window)
    assert results == []


def test_get_flights_id_has_window_date(provider: DemoProvider, window: Window) -> None:
    """Every returned flight ID contains the window start-date string."""
    results = provider.get_flights("DEL", "BOM", window)
    date_str = window.start_date.isoformat()
    for flight in results:
        assert date_str in flight.id, f"ID {flight.id!r} missing date {date_str!r}"


def test_get_flights_one_way_price(provider: DemoProvider, window: Window) -> None:
    """ONE_WAY prices must be less than ROUND_TRIP prices (~58% factor)."""
    rt_results = provider.get_flights("DEL", "BOM", window, trip_type=TripType.ROUND_TRIP)
    ow_results = provider.get_flights("DEL", "BOM", window, trip_type=TripType.ONE_WAY)

    assert len(rt_results) == len(ow_results) == 3

    rt_prices = {f.id.rsplit("-", 3)[0]: f.price_inr for f in rt_results}
    ow_prices = {f.id.rsplit("-", 3)[0]: f.price_inr for f in ow_results}

    for base_id, rt_price in rt_prices.items():
        assert ow_prices[base_id] < rt_price, (
            f"{base_id}: one-way {ow_prices[base_id]} not < round-trip {rt_price}"
        )


# ---------------------------------------------------------------------------
# Group 3 — Search→book identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_offer_id_accepted_by_revalidate(
    provider: DemoProvider, window: Window
) -> None:
    """offer_id returned by get_flights() is accepted by revalidate() without error."""
    results = provider.get_flights("DEL", "BOM", window)
    offer_id = results[0].id

    rv = await provider.revalidate(offer_id)

    assert rv.offer_id == offer_id
    assert rv.is_available is True


@pytest.mark.asyncio
async def test_search_offer_id_accepted_by_book(
    provider: DemoProvider, window: Window
) -> None:
    """offer_id for DEMO-FLT-001 from get_flights() books successfully."""
    results = provider.get_flights("DEL", "BOM", window)
    flt_001 = next(
        f for f in results if f.id.startswith("DEMO-FLT-001")
    )
    offer_id = flt_001.id

    result = await provider.book(offer_id, "key-001")

    assert isinstance(result, BookingResult)
    assert result.idempotency_key == "key-001"
    assert result.pnr.startswith("DEMO-PNR-")


# ---------------------------------------------------------------------------
# Group 4 — Full lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_loop_search_revalidate_book_cancel(window: Window) -> None:
    """Full end-to-end: search → revalidate → book → cancel (idempotent second cancel)."""
    provider = DemoProvider()

    # 1. Search
    results = provider.get_flights("DEL", "BOM", window)
    flt_001 = next(f for f in results if f.id.startswith("DEMO-FLT-001"))
    offer_id = flt_001.id

    # 2. Revalidate
    rv = await provider.revalidate(offer_id)
    assert rv.price_changed is False
    assert rv.is_available is True

    # 3. Book
    booking = await provider.book(offer_id, "test-key-loop")
    assert isinstance(booking, BookingResult)
    assert booking.pnr.startswith("DEMO-PNR-")

    # 4. Cancel
    cancel1 = await provider.cancel(booking.pnr)
    assert cancel1.cancelled is True

    # 5. Second cancel is idempotent — hold record still exists, just marked cancelled
    cancel2 = await provider.cancel(booking.pnr)
    assert cancel2.cancelled is True


# ---------------------------------------------------------------------------
# Group 5 — Price-changed trigger: BOTH LEGS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_changed_first_revalidate() -> None:
    """First revalidate of DEMO-FLT-005 returns price_changed=True at new price."""
    provider = DemoProvider()
    window = Window(start_date=date(2025, 6, 15), end_date=date(2025, 6, 22))
    offer_id = f"{PRICE_CHANGE_OFFER_ID}-{window.start_date.isoformat()}"

    result = await provider.revalidate(offer_id)

    assert result.price_changed is True
    assert result.current_price_inr == PRICE_CHANGE_NEW_PRICE
    assert result.previous_price_inr == PRICE_CHANGE_ORIGINAL_PRICE


@pytest.mark.asyncio
async def test_price_changed_second_revalidate_settles() -> None:
    """Second revalidate of DEMO-FLT-005 returns price_changed=False at settled price."""
    provider = DemoProvider()
    offer_id = f"{PRICE_CHANGE_OFFER_ID}-2025-06-15"

    await provider.revalidate(offer_id)  # first call — triggers price change
    result2 = await provider.revalidate(offer_id)  # second call — settled

    assert result2.price_changed is False
    assert result2.current_price_inr == PRICE_CHANGE_NEW_PRICE


@pytest.mark.asyncio
async def test_price_changed_second_attempt_books_successfully() -> None:
    """After price-change confirmation, the second revalidate allows a successful book."""
    provider = DemoProvider()
    offer_id = f"{PRICE_CHANGE_OFFER_ID}-2025-06-15"

    # First attempt: price-changed gate fires
    r1 = await provider.revalidate(offer_id)
    assert r1.price_changed is True

    # Second attempt: user confirmed at new price
    r2 = await provider.revalidate(offer_id)
    assert r2.price_changed is False

    # Now book proceeds cleanly
    result = await provider.book(offer_id, "key-price-confirmed")
    assert result.pnr.startswith("DEMO-PNR-")


# ---------------------------------------------------------------------------
# Group 6 — Idempotency + conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_book_returns_same_result(
    provider: DemoProvider, window: Window
) -> None:
    """Same (offer_id, idempotency_key) called twice → identical BookingResult."""
    offer_id = f"DEMO-FLT-001-{window.start_date.isoformat()}"

    result1 = await provider.book(offer_id, "idem-key-demo")
    result2 = await provider.book(offer_id, "idem-key-demo")

    assert result1.pnr == result2.pnr
    assert result1.offer_lock_id == result2.offer_lock_id
    assert result1.idempotency_key == result2.idempotency_key


@pytest.mark.asyncio
async def test_idempotency_conflict_different_offer(
    provider: DemoProvider, window: Window
) -> None:
    """Reusing an idempotency key with a different offer_id raises BookingConflictError."""
    offer_id_a = f"DEMO-FLT-001-{window.start_date.isoformat()}"
    offer_id_b = f"DEMO-FLT-004-{window.start_date.isoformat()}"

    await provider.book(offer_id_a, "conflict-key-demo")

    with pytest.raises(BookingConflictError, match="already bound"):
        await provider.book(offer_id_b, "conflict-key-demo")


@pytest.mark.asyncio
async def test_cancel_unknown_ref_returns_false(provider: DemoProvider) -> None:
    """cancel() on an unrecognised booking_ref returns cancelled=False."""
    result = await provider.cancel("NONEXISTENT-PNR")
    assert result.cancelled is False


# ---------------------------------------------------------------------------
# Group 7 — Unknown offer_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_offer_raises_inventory_client_error(
    provider: DemoProvider,
) -> None:
    """revalidate() on a completely unknown offer_id raises InventoryClientError."""
    with pytest.raises(InventoryClientError):
        await provider.revalidate("UNKNOWN-ID-999")


# ---------------------------------------------------------------------------
# Group 8 — close() clears state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_clears_state(provider: DemoProvider, window: Window) -> None:
    """After close(), _holds is empty and previously-booked PNRs are not found."""
    offer_id = f"DEMO-FLT-001-{window.start_date.isoformat()}"
    result = await provider.book(offer_id, "close-test-key")
    pnr = result.pnr

    assert len(provider._holds) == 1

    await provider.close()

    assert len(provider._holds) == 0

    # cancel returns False — PNR no longer known
    cancel_result = await provider.cancel(pnr)
    assert cancel_result.cancelled is False
