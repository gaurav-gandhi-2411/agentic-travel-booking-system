"""Tenant API key auth middleware.

Resolves the Bearer token or X-API-Key header to a Tenant row, sets
request.state.tenant_id / user_id, and enforces auth on /search and /refine.

In APP_MODE=local|synthetic, auth is bypassed and synthetic tenant context
("local"/"local") is injected so downstream code that reads those fields works.

The DEMO_API_KEY env var continues to authenticate via the seeded demo tenant
(backward compat — no code changes needed in callers).
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from travel_agent.persistence.engine import get_session_factory
from travel_agent.tenancy.service import resolve_key

_GUARDED_PREFIXES = ("/search", "/refine", "/book", "/cancel")
_LOCAL_MODES = {"local", "synthetic"}


class TenantAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._mode = os.environ.get("APP_MODE", "demo").lower()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not any(request.url.path.startswith(p) for p in _GUARDED_PREFIXES):
            return await call_next(request)

        if self._mode in _LOCAL_MODES:
            request.state.tenant_id = "local"
            request.state.user_id = "local"
            # Booking routes default to mock_bookable so /book reaches MockBookableProvider.
            # Search/refine keep "aviasales" (falls through to SyntheticProvider without
            # AVIASALES_LIVE=true — existing documented behavior preserved).
            # Set MOCK_INVENTORY_ADAPTER=aviasales to test the not_bookable booking path.
            if any(request.url.path.startswith(p) for p in ("/book", "/cancel")):
                request.state.inventory_adapter = os.environ.get(
                    "MOCK_INVENTORY_ADAPTER", "mock_bookable"
                )
            else:
                request.state.inventory_adapter = "aviasales"
            request.state.affiliate_enabled = True
            return await call_next(request)

        raw_key = _extract_key(request)
        if not raw_key:
            return JSONResponse(
                {
                    "detail": (
                        "Missing API key. Use Authorization: Bearer <key> or X-API-Key header."
                    )
                },
                status_code=401,
            )

        factory = get_session_factory()
        async with factory() as session:
            tenant = await resolve_key(raw_key, session)

        if tenant is None:
            return JSONResponse({"detail": "Invalid or inactive API key."}, status_code=401)

        request.state.tenant_id = str(tenant.id)
        # user_id == tenant_id until a per-user model exists
        request.state.user_id = str(tenant.id)
        request.state.inventory_adapter = tenant.inventory_adapter
        request.state.affiliate_enabled = tenant.affiliate_enabled
        return await call_next(request)


def _extract_key(request: Request) -> str | None:
    """Extract the raw API key from Authorization: Bearer or X-API-Key header."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.headers.get("X-API-Key") or None
