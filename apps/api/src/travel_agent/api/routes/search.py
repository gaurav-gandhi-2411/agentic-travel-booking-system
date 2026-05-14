"""POST /search — SSE streaming flight search endpoint.

Request:  {"query": "fly from Mumbai to Paris next month"}
Response: text/event-stream, one JSON event per line.

See coordinator/streaming.py for the full event sequence spec.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.streaming import stream_search
from travel_agent.llm.anthropic import AnthropicAdapter

router = APIRouter()


class SearchRequest(BaseModel):
    query: str


def _build_agents() -> tuple[PlannerAgent, OptimizerAgent]:
    llm = AnthropicAdapter()
    planner = PlannerAgent(llm, "claude-haiku-4-5-20251001")
    optimizer = OptimizerAgent(
        client=llm,
        model="claude-sonnet-4-6",
        partner_marker=os.environ.get("AVIASALES_PARTNER_ID", ""),
    )
    return planner, optimizer


async def _sse_generator(query: str) -> AsyncGenerator[str, None]:
    planner, optimizer = _build_agents()
    async for event in stream_search(query, planner, optimizer):
        yield f"data: {json.dumps(event)}\n\n"


@router.post("/search")
async def search(body: SearchRequest) -> StreamingResponse:
    return StreamingResponse(
        _sse_generator(body.query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
