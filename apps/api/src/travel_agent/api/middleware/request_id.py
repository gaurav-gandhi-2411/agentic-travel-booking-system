"""Request-ID middleware — injects X-Request-ID into every request/response cycle.

Generates a UUID if the client does not supply one. Binds the ID to the structlog
contextvars store so every log line emitted during the request carries request_id
without callers needing to thread it explicitly.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
import structlog.contextvars
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_HEADER = "X-Request-ID"

_RequestResponseEndpoint = Callable[[Request], Awaitable[Response]]


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Ensure every request has a unique ID, bound to structlog context."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: _RequestResponseEndpoint) -> Response:
        req_id = request.headers.get(_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=req_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers[_HEADER] = req_id
        return response
