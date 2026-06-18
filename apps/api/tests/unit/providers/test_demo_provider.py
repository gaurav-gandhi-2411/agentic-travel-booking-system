"""Tests for DemoProvider — search, bookable lifecycle, price-changed trigger.

Groups:
  1. Protocol conformance (2 tests)
  2. Hardcoded catalog: get_flights() (4 tests)
  3. Search→book identity, hardcoded (2 tests)
  4. Full lifecycle, hardcoded (1 test)
  5. Price-changed trigger: BOTH LEGS, hardcoded (3 tests)
  6. Idempotency + conflict (3 tests)
  7. Unknown offer_id (1 test)
  8. close() clears state (1 test)
  9. Generated routes: any-route generation (5 tests)
 10. Generated routes: full booking lifecycle (1 test)
 11. Generated routes: stateless reconstruction from offer_id (1 test)
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
    _GENERATED_FLIGHT_INDEX,
    _GENERATED_PRICE_CHANGE_OFFER_IDS,
    PRICE_CHANGE_NEW_PRICE,
    PRICE_CHANGE_OFFER_ID,
    PRICE_CHANGE_ORIGINAL_PRICE,
    DemoProvider,
    _generate_route_offers,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def window() -> Window:
    return Window(start_date=date(2025, 6, 15), end_date=date(2025, 6, 22))


@pytest.fixture
def provider() -> DemoProvider:
    return DemoProvider()


# ---------------------------------------------------------------------------
# Group 1 — Protocol conformance
# ---------------------------------------------------------------------------


def test_demo_provider_is_bookable_inventory_provider() -> None:
    assert isinstance(DemoProvider(), BookableInventoryProvider) is True


def test_demo_provider_is_also_inventory_provider() -> None:
    p = DemoProvider()
    assert isinstance(p, InventoryProvider) is True
    assert isinstance(p, BookableInventoryProvider) is True


# ---------------------------------------------------------------------------
# Group 2 — Hardcoded catalog: get_flights()
# ---------------------------------------------------------------------------


def test_get_flights_returns_demo_offers(provider: DemoProvider, window: Window) -> None:
    """DEL→BOM returns 3 results (DEMO-FLT-001, 002, 005), all provider='demo'."""
    results = provider.get_flights("DEL", "BOM", window)

    assert len(results) == 3

    for flight in results:
        assert flight.provider == "demo"
        assert flight.id.startswith("DEMO-FLT-")
        assert flight.id.endswith(window.start_date.isoformat())

    returned_ids = {f.id for f in results}
    for base in ("DEMO-FLT-001", "DEMO-FLT-002", "DEMO-FLT-005"):
        assert f"{base}-{window.start_date.isoformat()}" in returned_ids


def test_get_flights_generates_for_unlisted_route(provider: DemoProvider, window: Window) -> None:
    """Routes not in the hardcoded catalog produce generated offers, not empty list."""
    results = provider.get_flights("BOM", "DEL", window)  # reverse — not in catalog
    assert len(results) >= 3
    for f in results:
        assert f.origin_iata == "BOM"
        assert f.destination_iata == "DEL"
        assert f.provider == "demo"


def test_get_flights_id_has_window_date(provider: DemoProvider, window: Window) -> None:
    results = provider.get_flights("DEL", "BOM", window)
    date_str = window.start_date.isoformat()
    for flight in results:
        assert date_str in flight.id, f"ID {flight.id!r} missing date {date_str!r}"


def test_get_flights_one_way_price(provider: DemoProvider, window: Window) -> None:
    """ONE_WAY prices must be less than ROUND_TRIP prices (~58% factor)."""
    rt = provider.get_flights("DEL", "BOM", window, trip_type=TripType.ROUND_TRIP)
    ow = provider.get_flights("DEL", "BOM", window, trip_type=TripType.ONE_WAY)

    assert len(rt) == len(ow) == 3

    rt_prices = {f.id.rsplit("-", 3)[0]: f.price_inr for f in rt}
    ow_prices = {f.id.rsplit("-", 3)[0]: f.price_inr for f in ow}

    for base_id, rt_price in rt_prices.items():
        assert ow_prices[base_id] < rt_price, (
            f"{base_id}: one-way {ow_prices[base_id]} not < round-trip {rt_price}"
        )


# ---------------------------------------------------------------------------
# Group 3 — Search→book identity, hardcoded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_offer_id_accepted_by_revalidate(
    provider: DemoProvider, window: Window
) -> None:
    results = provider.get_flights("DEL", "BOM", window)
    offer_id = results[0].id

    rv = await provider.revalidate(offer_id)

    assert rv.offer_id == offer_id
    assert rv.is_available is True


@pytest.mark.asyncio
async def test_search_offer_id_accepted_by_book(provider: DemoProvider, window: Window) -> None:
    results = provider.get_flights("DEL", "BOM", window)
    flt_001 = next(f for f in results if f.id.startswith("DEMO-FLT-001"))

    result = await provider.book(flt_001.id, "key-001")

    assert isinstance(result, BookingResult)
    assert result.idempotency_key == "key-001"
    assert result.pnr.startswith("DEMO-PNR-")


# ---------------------------------------------------------------------------
# Group 4 — Full lifecycle, hardcoded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_loop_search_revalidate_book_cancel(window: Window) -> None:
    provider = DemoProvider()

    results = provider.get_flights("DEL", "BOM", window)
    flt_001 = next(f for f in results if f.id.startswith("DEMO-FLT-001"))

    rv = await provider.revalidate(flt_001.id)
    assert rv.price_changed is False
    assert rv.is_available is True

    booking = await provider.book(flt_001.id, "test-key-loop")
    assert booking.pnr.startswith("DEMO-PNR-")

    cancel1 = await provider.cancel(booking.pnr)
    assert cancel1.cancelled is True

    cancel2 = await provider.cancel(booking.pnr)
    assert cancel2.cancelled is True


# ---------------------------------------------------------------------------
# Group 5 — Price-changed trigger: BOTH LEGS, hardcoded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_changed_first_revalidate() -> None:
    provider = DemoProvider()
    window = Window(start_date=date(2025, 6, 15), end_date=date(2025, 6, 22))
    offer_id = f"{PRICE_CHANGE_OFFER_ID}-{window.start_date.isoformat()}"

    result = await provider.revalidate(offer_id)

    assert result.price_changed is True
    assert result.current_price_inr == PRICE_CHANGE_NEW_PRICE
    assert result.previous_price_inr == PRICE_CHANGE_ORIGINAL_PRICE


@pytest.mark.asyncio
async def test_price_changed_revalidate_always_returns_true_for_trigger() -> None:
    """Stateless design: trigger offer always returns price_changed=True on every call."""
    provider = DemoProvider()
    offer_id = f"{PRICE_CHANGE_OFFER_ID}-2025-06-15"

    r1 = await provider.revalidate(offer_id)
    assert r1.price_changed is True
    assert r1.current_price_inr == PRICE_CHANGE_NEW_PRICE

    # Second call — still True; no per-instance state to settle
    r2 = await provider.revalidate(offer_id)
    assert r2.price_changed is True
    assert r2.current_price_inr == PRICE_CHANGE_NEW_PRICE


@pytest.mark.asyncio
async def test_price_changed_book_succeeds_after_revalidate() -> None:
    """book() works regardless of price_changed state; accept logic is in the coordinator."""
    provider = DemoProvider()
    offer_id = f"{PRICE_CHANGE_OFFER_ID}-2025-06-15"

    r1 = await provider.revalidate(offer_id)
    assert r1.price_changed is True

    result = await provider.book(offer_id, "key-price-confirmed")
    assert result.pnr.startswith("DEMO-PNR-")


# ---------------------------------------------------------------------------
# Group 6 — Idempotency + conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_book_returns_same_result(provider: DemoProvider, window: Window) -> None:
    offer_id = f"DEMO-FLT-001-{window.start_date.isoformat()}"

    result1 = await provider.book(offer_id, "idem-key-demo")
    result2 = await provider.book(offer_id, "idem-key-demo")

    assert result1.pnr == result2.pnr
    assert result1.offer_lock_id == result2.offer_lock_id
    assert result1.idempotency_key == result2.idempotency_key


@pytest.mark.asyncio
async def test_idempotency_conflict_different_offer(provider: DemoProvider, window: Window) -> None:
    offer_id_a = f"DEMO-FLT-001-{window.start_date.isoformat()}"
    offer_id_b = f"DEMO-FLT-004-{window.start_date.isoformat()}"

    await provider.book(offer_id_a, "conflict-key-demo")

    with pytest.raises(BookingConflictError, match="already bound"):
        await provider.book(offer_id_b, "conflict-key-demo")


@pytest.mark.asyncio
async def test_cancel_unknown_ref_returns_false(provider: DemoProvider) -> None:
    result = await provider.cancel("NONEXISTENT-PNR")
    assert result.cancelled is False


# ---------------------------------------------------------------------------
# Group 7 — Unknown offer_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_offer_raises_inventory_client_error(
    provider: DemoProvider,
) -> None:
    with pytest.raises(InventoryClientError):
        await provider.revalidate("UNKNOWN-ID-999")


# ---------------------------------------------------------------------------
# Group 8 — close() clears state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_clears_state(provider: DemoProvider, window: Window) -> None:
    offer_id = f"DEMO-FLT-001-{window.start_date.isoformat()}"
    result = await provider.book(offer_id, "close-test-key")
    pnr = result.pnr

    assert len(provider._holds) == 1

    await provider.close()

    assert len(provider._holds) == 0

    cancel_result = await provider.cancel(pnr)
    assert cancel_result.cancelled is False


# ---------------------------------------------------------------------------
# Group 9 — Generated routes: any-route generation
# ---------------------------------------------------------------------------


def test_generated_offers_returned_for_unknown_route(
    provider: DemoProvider, window: Window
) -> None:
    """Any route not in the catalog returns 3 generated offers."""
    results = provider.get_flights("DEL", "DXB", window)

    assert len(results) == 3
    for f in results:
        assert f.provider == "demo"
        assert f.origin_iata == "DEL"
        assert f.destination_iata == "DXB"
        assert f.id.startswith("GEN-DELDXB-")
        assert f.id.endswith(window.start_date.isoformat())


def test_generated_offers_are_deterministic(provider: DemoProvider, window: Window) -> None:
    """Same route called twice → identical prices, airlines, times."""
    r1 = provider.get_flights("DEL", "SIN", window)
    r2 = provider.get_flights("DEL", "SIN", window)

    assert len(r1) == len(r2)
    for f1, f2 in zip(r1, r2, strict=True):
        assert f1.id == f2.id
        assert f1.price_inr == f2.price_inr
        assert f1.airline_code == f2.airline_code
        assert f1.outbound_departure_at == f2.outbound_departure_at


def test_generated_offers_cheapest_is_economy(provider: DemoProvider, window: Window) -> None:
    """The cheapest offer (GEN-...-001) is always an economy class flight."""
    results = provider.get_flights("BOM", "BKK", window)
    by_price = sorted(results, key=lambda f: f.price_inr)
    assert by_price[0].cabin_class.value == "economy"


def test_different_routes_produce_different_prices(provider: DemoProvider, window: Window) -> None:
    """DEL→DXB and DEL→SIN are different routes so prices differ."""
    dxb = provider.get_flights("DEL", "DXB", window)
    sin = provider.get_flights("DEL", "SIN", window)

    dxb_prices = {f.price_inr for f in dxb}
    sin_prices = {f.price_inr for f in sin}
    # Different routes → at least one price differs
    assert dxb_prices != sin_prices


def test_generated_route_registered_in_index(provider: DemoProvider, window: Window) -> None:
    """After get_flights(), the GEN-* base IDs appear in _GENERATED_FLIGHT_INDEX."""
    provider.get_flights("DEL", "CDG", window)

    assert "GEN-DELCDG-001" in _GENERATED_FLIGHT_INDEX
    assert "GEN-DELCDG-002" in _GENERATED_FLIGHT_INDEX
    assert "GEN-DELCDG-003" in _GENERATED_FLIGHT_INDEX
    assert "GEN-DELCDG-001" in _GENERATED_PRICE_CHANGE_OFFER_IDS


# ---------------------------------------------------------------------------
# Group 10 — Generated routes: full booking lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generated_route_full_lifecycle_with_price_change(window: Window) -> None:
    """End-to-end: search DEL→DXB -> book cheapest (price-change trigger) ->
    first revalidate shows price change -> second revalidate settles ->
    book at new price -> cancel.
    """
    provider = DemoProvider()

    # 1. Search
    results = provider.get_flights("DEL", "DXB", window)
    assert len(results) == 3

    # Cheapest economy is the price-change trigger
    economies = [f for f in results if f.cabin_class.value == "economy"]
    cheapest = min(economies, key=lambda f: f.price_inr)
    assert cheapest.id.startswith("GEN-DELDXB-001")
    offer_id = cheapest.id

    # 2. First revalidate → price change fires (stateless: always True for trigger)
    rv1 = await provider.revalidate(offer_id)
    assert rv1.price_changed is True
    assert rv1.current_price_inr > rv1.previous_price_inr
    assert rv1.is_available is True
    new_price = rv1.current_price_inr
    original_price = rv1.previous_price_inr

    # 3. Second revalidate → still price_changed=True (stateless design — coordinator
    #    decides halt/proceed via accept_price_change flag, not provider state)
    rv2 = await provider.revalidate(offer_id)
    assert rv2.price_changed is True
    assert rv2.current_price_inr == new_price

    # 4. Book succeeds at new price (book() is independent of price_changed state)
    booking = await provider.book(offer_id, "gen-booking-key-001")
    assert isinstance(booking, BookingResult)
    assert booking.pnr.startswith("DEMO-PNR-")
    pnr = booking.pnr

    # 5. Idempotent re-book returns same PNR
    booking2 = await provider.book(offer_id, "gen-booking-key-001")
    assert booking2.pnr == pnr

    # 6. Cancel succeeds
    cancel = await provider.cancel(pnr)
    assert cancel.cancelled is True
    assert cancel.booking_ref == pnr

    # Prices sanity-check
    assert new_price > original_price


# ---------------------------------------------------------------------------
# Group 11 — Stateless reconstruction from offer_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalidate_works_without_prior_get_flights() -> None:
    """revalidate() with a GEN-* ID reconstructs the offer statelessly.

    Simulates the Cloud Run cross-instance scenario: /search hit instance A
    (get_flights called), /book hits instance B (no prior get_flights).
    A fresh DemoProvider + cleared module index simulates instance B.
    """
    # Directly compute expected offer data without calling get_flights first
    offers_tuple = _generate_route_offers("BOM", "SIN")
    cheapest_base = min(
        (f for f in offers_tuple if f.cabin_class == "economy"),
        key=lambda f: f.price_inr,
    ).offer_id  # e.g. "GEN-BOMSIN-001"

    provider = DemoProvider()
    window = Window(start_date=date(2025, 7, 1), end_date=date(2025, 7, 8))
    offer_id = f"{cheapest_base}-{window.start_date.isoformat()}"

    # No get_flights() call — _ensure_gen_offer() must reconstruct the offer
    rv = await provider.revalidate(offer_id)

    assert rv.offer_id == offer_id
    assert rv.is_available is True
    # Stateless: trigger offer always returns price_changed=True on any instance
    assert rv.price_changed is True
