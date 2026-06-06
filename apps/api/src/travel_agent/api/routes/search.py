"""POST /search — SSE streaming flight search endpoint.

Request:  {"query": "fly from Mumbai to Paris next month"}
Response: text/event-stream, one JSON event per line.

The X-LLM-Profile request header selects the LLM provider for this request:
  demo-haiku  → Anthropic Claude Haiku (default when env profile is "demo")
  demo-llama  → Groq Llama 3.3 70B (free tier)
  demo-qwen   → OpenRouter Qwen 2.5 72B (free tier)
  (absent)    → falls back to LLM_ROUTING_PROFILE env var

See coordinator/streaming.py for the full event sequence spec.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncGenerator

import structlog.contextvars
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.streaming import stream_search
from travel_agent.llm import get_llm_client_and_model
from travel_agent.observability.langfuse_client import get_langfuse, set_request_trace

router = APIRouter()

_ALLOWED_PROFILES: frozenset[str] = frozenset({"demo-haiku", "demo-llama", "demo-qwen"})


def _resolve_profile(requested: str | None) -> str:
    """Map the per-request X-LLM-Profile value to a valid routing profile name."""
    if requested in _ALLOWED_PROFILES:
        return requested
    env = os.environ.get("LLM_ROUTING_PROFILE", "demo")
    return "demo-haiku" if env == "demo" else env


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)


def _build_agents(profile: str) -> tuple[PlannerAgent, OptimizerAgent]:
    planner_client, planner_model = get_llm_client_and_model("planner", profile)
    optimizer_client, optimizer_model = get_llm_client_and_model("optimizer", profile)
    planner = PlannerAgent(planner_client, planner_model)
    _affiliate_on = os.environ.get("AFFILIATE_DEEPLINKS", "true").lower() not in ("false", "0")
    optimizer = OptimizerAgent(
        client=optimizer_client,
        model=optimizer_model,
        partner_marker=os.environ.get("AVIASALES_PARTNER_ID", "") if _affiliate_on else "",
    )
    return planner, optimizer


async def _sse_generator(query: str, profile: str, request_id: str) -> AsyncGenerator[str, None]:
    # Langfuse trace — optional, never breaks the pipeline
    lf = get_langfuse()
    trace = None
    with contextlib.suppress(Exception):
        if lf is not None:
            trace = lf.start_observation(
                name="search",
                as_type="span",
                input={"query": query, "profile": profile},
                metadata={"request_id": request_id},
            )
            set_request_trace(trace)

    try:
        planner, optimizer = _build_agents(profile)
    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        return
    async for event in stream_search(query, planner, optimizer):
        yield f"data: {json.dumps(event)}\n\n"

    # End trace and flush
    with contextlib.suppress(Exception):
        if lf is not None and trace is not None:
            trace.end()
            lf.flush()


@router.post("/search")
async def search(body: SearchRequest, request: Request) -> StreamingResponse:
    llm_profile = getattr(request.state, "llm_profile", None)
    profile = _resolve_profile(llm_profile)
    request_id = str(structlog.contextvars.get_contextvars().get("request_id", ""))
    return StreamingResponse(
        _sse_generator(body.query, profile, request_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
