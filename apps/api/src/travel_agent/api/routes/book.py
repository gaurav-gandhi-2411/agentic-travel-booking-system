"""POST /book and POST /cancel — booking lifecycle endpoints.

Both endpoints are SSE streams that emit booking lifecycle events.
Both check the tenant's capability gate (bookable vs search-only) before
attempting any provider call.

SSE event types are defined in coordinator.booking_streaming.BookingEventType.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from travel_agent.coordinator.booking_streaming import (
    BookingEventType,
    stream_book,
    stream_cancel,
)
from travel_agent.providers.factory import get_bookable_provider

router = APIRouter()


class BookRequest(BaseModel):
    offer_id: str
    idempotency_key: str
    request_id: str | None = None  # links back to a prior /search for audit trail


class CancelRequest(BaseModel):
    booking_ref: str


def _not_bookable_event(slug: str) -> dict[str, object]:
    return {
        "type": BookingEventType.BOOKING_ERROR,
        "message": f"booking not supported by inventory source {slug!r}",
        "code": "not_bookable",
    }


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


async def _sse_book_generator(
    body: BookRequest,
    inventory_adapter: str,
) -> AsyncGenerator[str, None]:
    provider = get_bookable_provider(inventory_adapter)
    if provider is None:
        yield f"data: {json.dumps(_not_bookable_event(inventory_adapter))}\n\n"
        return
    async for event in stream_book(body.offer_id, body.idempotency_key, provider):
        yield f"data: {json.dumps(event)}\n\n"


async def _sse_cancel_generator(
    body: CancelRequest,
    inventory_adapter: str,
) -> AsyncGenerator[str, None]:
    provider = get_bookable_provider(inventory_adapter)
    if provider is None:
        yield f"data: {json.dumps(_not_bookable_event(inventory_adapter))}\n\n"
        return
    async for event in stream_cancel(body.booking_ref, provider):
        yield f"data: {json.dumps(event)}\n\n"


@router.post("/book")
async def book(body: BookRequest, request: Request) -> StreamingResponse:
    """Revalidate → book → confirm. SSE stream of booking lifecycle events."""
    inventory_adapter: str = getattr(request.state, "inventory_adapter", "aviasales")
    return StreamingResponse(
        _sse_book_generator(body, inventory_adapter),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/cancel")
async def cancel(body: CancelRequest, request: Request) -> StreamingResponse:
    """Release a hold by booking_ref. SSE stream of cancellation lifecycle events."""
    inventory_adapter: str = getattr(request.state, "inventory_adapter", "aviasales")
    return StreamingResponse(
        _sse_cancel_generator(body, inventory_adapter),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
