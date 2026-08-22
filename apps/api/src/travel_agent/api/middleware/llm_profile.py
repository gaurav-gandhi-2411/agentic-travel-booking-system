"""LLM profile selection middleware.

Reads the X-LLM-Profile request header, validates it against the allowed set,
and stores the result in request.state.llm_profile.

Route handlers read request.state.llm_profile to select the correct provider
and model for that one request. Any invalid or missing header leaves the field
as None, which causes the route to fall back to the env-default profile.

Allowed header values: demo-haiku, demo-llama, demo-gpt-oss-120b, demo-qwen.
"""

from __future__ import annotations

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

# demo-gpt-oss-120b added 2026-08-22: the frontend's ProfileToggle.tsx defaults to this
# value, but it was missing here, so the header was silently rejected at the edge before
# routes/search.py and routes/refine.py's own _resolve_profile ever saw it -- every
# request fell through to the LLM_ROUTING_PROFILE env default regardless of user choice.
# demo-qwen is kept even though the frontend no longer offers it (demoted profile,
# commented out in llm_routing.yaml) -- out of scope to remove here.
ALLOWED_PROFILES: frozenset[str] = frozenset(
    {"demo-haiku", "demo-llama", "demo-gpt-oss-120b", "demo-qwen"}
)


class LLMProfileMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw = request.headers.get("X-LLM-Profile", "").strip()
        request.state.llm_profile = raw if raw in ALLOWED_PROFILES else None
        return await call_next(request)
