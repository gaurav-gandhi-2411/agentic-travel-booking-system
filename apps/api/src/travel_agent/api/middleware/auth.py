"""Demo API key auth middleware.

Checks X-API-Key header against DEMO_API_KEY env var.
Only enforced when APP_MODE=demo.  In APP_MODE=local|synthetic, all requests pass.

Protects the /search endpoint from unauthenticated use in the public demo.
"""
from __future__ import annotations

import os

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class DemoAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        app_mode = os.environ.get("APP_MODE", "synthetic")
        if app_mode != "demo":
            return await call_next(request)

        # Only guard /search — health check must remain open for Cloud Run probes
        if not request.url.path.startswith("/search"):
            return await call_next(request)

        expected_key = os.environ.get("DEMO_API_KEY", "")
        if not expected_key:
            # No key configured — pass through (dev convenience)
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key", "")
        if provided_key != expected_key:
            return Response(
                content='{"detail":"Unauthorized"}',
                status_code=401,
                media_type="application/json",
            )
        return await call_next(request)
