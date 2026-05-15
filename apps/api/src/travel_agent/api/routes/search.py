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

import json
import os
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.streaming import stream_search
from travel_agent.llm import get_llm_client_and_model

router = APIRouter()

_ALLOWED_PROFILES: frozenset[str] = frozenset({"demo-haiku", "demo-llama", "demo-qwen"})


def _resolve_profile(requested: str | None) -> str:
    """Map the per-request X-LLM-Profile value to a valid routing profile name."""
    if requested in _ALLOWED_PROFILES:
        return requested
    env = os.environ.get("LLM_ROUTING_PROFILE", "demo")
    return "demo-haiku" if env == "demo" else env


class SearchRequest(BaseModel):
    query: str


def _build_agents(profile: str) -> tuple[PlannerAgent, OptimizerAgent]:
    planner_client, planner_model = get_llm_client_and_model("planner", profile)
    optimizer_client, optimizer_model = get_llm_client_and_model("optimizer", profile)
    planner = PlannerAgent(planner_client, planner_model)
    optimizer = OptimizerAgent(
        client=optimizer_client,
        model=optimizer_model,
        partner_marker=os.environ.get("AVIASALES_PARTNER_ID", ""),
    )
    return planner, optimizer


async def _sse_generator(query: str, profile: str) -> AsyncGenerator[str, None]:
    try:
        planner, optimizer = _build_agents(profile)
    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        return
    async for event in stream_search(query, planner, optimizer):
        yield f"data: {json.dumps(event)}\n\n"


@router.post("/search")
async def search(body: SearchRequest, request: Request) -> StreamingResponse:
    llm_profile = getattr(request.state, "llm_profile", None)
    profile = _resolve_profile(llm_profile)
    return StreamingResponse(
        _sse_generator(body.query, profile),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
