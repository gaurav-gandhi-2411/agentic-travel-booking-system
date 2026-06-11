"""Unit tests for BookingAgent (Phase 3.2-E.1).

Boundary: provider is always mocked. These tests verify that BookingAgent.run()
correctly maps a BookingResult onto RequestState.booking and sets state.phase.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from travel_agent.agents.booking import BookingAgent
from travel_agent.coordinator.state import BookingPhase, CoordinatorPhase, RequestState
from travel_agent.providers.base import BookingConflictError, BookingResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    *,
    pnr: str = "MOCK-PNR-ABCD1234",
    offer_lock_id: str = "MOCK-LOCK-EFGH5678",
    hold_expires_at: str = "2026-12-31T23:59:59+00:00",
    idempotency_key: str = "idem-test-001",
    audit_id: UUID | None = None,
) -> BookingResult:
    return BookingResult(
        pnr=pnr,
        offer_lock_id=offer_lock_id,
        hold_expires_at=hold_expires_at,
        idempotency_key=idempotency_key,
        audit_id=audit_id,
    )


def _make_provider(result: BookingResult) -> MagicMock:
    """Return a mock provider whose book() awaits to *result*."""
    provider = MagicMock()
    provider.book = AsyncMock(return_value=result)
    return provider


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_maps_booking_result_to_state() -> None:
    """BookingAgent.run() maps every BookingResult field onto state.booking."""
    result = _make_result()
    provider = _make_provider(result)
    agent = BookingAgent(provider)
    state = RequestState()

    updated = await agent.run(state, offer_id="MOCK-OFFER-001", idempotency_key="idem-test-001")

    assert updated.booking is not None
    assert updated.booking.phase == BookingPhase.LOCKED
    assert updated.booking.pnr == result.pnr
    assert updated.booking.offer_lock_id == result.offer_lock_id
    assert updated.booking.hold_expires_at == result.hold_expires_at
    assert updated.booking.idempotency_key == result.idempotency_key
    assert updated.booking.audit_id is None
    assert updated.phase == CoordinatorPhase.BOOKING


async def test_run_maps_audit_id() -> None:
    """When BookingResult carries a non-None audit_id, state.booking.audit_id matches."""
    audit_id = UUID("12345678-1234-5678-1234-567812345678")
    result = _make_result(audit_id=audit_id)
    provider = _make_provider(result)
    agent = BookingAgent(provider)

    updated = await agent.run(RequestState(), offer_id="MOCK-OFFER-001", idempotency_key="k-1")

    assert updated.booking is not None
    assert updated.booking.audit_id == audit_id


async def test_run_propagates_booking_conflict_error() -> None:
    """BookingConflictError raised by provider.book() is NOT swallowed by BookingAgent."""
    provider = MagicMock()
    provider.book = AsyncMock(
        side_effect=BookingConflictError("already bound to a different offer")
    )
    agent = BookingAgent(provider)

    with pytest.raises(BookingConflictError, match="already bound"):
        await agent.run(RequestState(), offer_id="MOCK-OFFER-002", idempotency_key="conflict-key")


async def test_run_calls_book_with_correct_args() -> None:
    """BookingAgent.run() forwards offer_id and idempotency_key to provider.book()."""
    result = _make_result(pnr="MOCK-PNR-XYZ", idempotency_key="key-999")
    provider = _make_provider(result)
    agent = BookingAgent(provider)

    await agent.run(RequestState(), offer_id="MOCK-OFFER-003", idempotency_key="key-999")

    provider.book.assert_awaited_once_with("MOCK-OFFER-003", "key-999")
