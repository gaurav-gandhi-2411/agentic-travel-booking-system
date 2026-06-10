"""BookingAgent — HITL booking: calls provider.book() and maps result to BookingStatus."""

from __future__ import annotations

from travel_agent.coordinator.state import (
    BookingPhase,
    BookingStatus,
    CoordinatorPhase,
    RequestState,
)
from travel_agent.providers.base import BookableInventoryProvider


class BookingAgent:
    def __init__(self, provider: BookableInventoryProvider) -> None:
        self._provider = provider

    async def run(
        self, state: RequestState, offer_id: str, idempotency_key: str
    ) -> RequestState:
        """Call provider.book() and map the BookingResult into state.booking.

        Args:
            state: Current request state (mutated in place and returned).
            offer_id: The inventory offer to hold.
            idempotency_key: Caller-supplied deduplication key.

        Returns:
            The updated RequestState with booking populated and phase set to BOOKING.
        """
        result = await self._provider.book(offer_id, idempotency_key)
        state.booking = BookingStatus(
            phase=BookingPhase.LOCKED,
            offer_lock_id=result.offer_lock_id,
            hold_expires_at=result.hold_expires_at,
            idempotency_key=result.idempotency_key,
            pnr=result.pnr,
            audit_id=result.audit_id,
        )
        state.phase = CoordinatorPhase.BOOKING
        return state
