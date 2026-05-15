"""POST /refine — in-memory re-filter + re-optimize endpoint.

Re-uses the cached flight list from /search (no new provider calls) to apply
simple refinement filters, then re-runs OptimizerAgent and streams new archetypes.

Supported change types (keyword-matched, instant):
  cheaper       — keep flights at or below median price
  skip_red_eyes — exclude departures before 06:00
  non_stop      — exclude flights with any layovers

Any unrecognised refinement falls back to re-running the full pipeline via
stream_search so the user never gets a dead end.

Event sequence (same SSE schema as /search):
  refine_started  {refinement, change_type}
  optimizer_started
  archetype_ready {archetype}   (x2)
  done            {request_id}
  error           {message}
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
from travel_agent.api.cache import search_cache
from travel_agent.coordinator.state import FlightOption, RequestState
from travel_agent.coordinator.streaming import stream_search
from travel_agent.llm import get_llm_client_and_model

router = APIRouter()

_RED_EYE_CUTOFF_HOUR = 6


_ALLOWED_PROFILES: frozenset[str] = frozenset({"demo-haiku", "demo-free"})


def _resolve_profile(requested: str | None) -> str:
    if requested in _ALLOWED_PROFILES:
        return requested
    env = os.environ.get("LLM_ROUTING_PROFILE", "demo")
    return "demo-haiku" if env == "demo" else env


class RefineRequest(BaseModel):
    request_id: str
    refinement: str


def _parse_change_type(text: str) -> str:
    lower = text.lower()
    cheap_kws = ["cheap", "cheaper", "budget", "affordable", "less expensive", "lower price"]
    if any(w in lower for w in cheap_kws):
        return "cheaper"
    red_eye_kws = [
        "red-eye",
        "red eye",
        "redeye",
        "red_eye",
        "skip_red_eyes",
        "skip early",
        "no early",
        "no red",
    ]
    if any(w in lower for w in red_eye_kws):
        return "skip_red_eyes"
    nonstop_kws = [
        "non-stop",
        "nonstop",
        "non_stop",
        "direct",
        "no stop",
        "no layover",
        "without stop",
    ]
    if any(w in lower for w in nonstop_kws):
        return "non_stop"
    return "full_search"


def _filter_flights(flights: list[FlightOption], change_type: str) -> list[FlightOption]:
    if change_type == "cheaper":
        if len(flights) <= 1:
            return flights
        prices = sorted(f.price_inr for f in flights)
        threshold = prices[len(prices) // 2]  # median
        filtered = [f for f in flights if f.price_inr <= threshold]
        return filtered if filtered else flights

    if change_type == "skip_red_eyes":

        def _hour(iso: str) -> int:
            try:
                return int(iso[11:13])
            except (ValueError, IndexError):
                return 12

        filtered = [f for f in flights if _hour(f.outbound_departure_at) >= _RED_EYE_CUTOFF_HOUR]
        return filtered if filtered else flights

    if change_type == "non_stop":
        filtered = [f for f in flights if f.layover_count == 0]
        return filtered if filtered else flights

    return flights


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


async def _refine_generator(
    request_id: str,
    refinement: str,
    profile: str,
) -> AsyncGenerator[str, None]:
    def _event(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    cached = search_cache.get(request_id)
    change_type = _parse_change_type(refinement)

    # Cache miss or full-search refinement → delegate to stream_search
    if cached is None or change_type == "full_search":
        try:
            planner, optimizer = _build_agents(profile)
        except Exception as exc:
            yield _event({"type": "error", "message": str(exc)})
            return
        # Re-run full search using original query from refinement text if no cache
        query = refinement
        if cached is not None:
            intent = cached[0]
            query = f"{intent.origin_iata} to {intent.destination_iata} {refinement}"
        async for evt in stream_search(query, planner, optimizer):
            yield _event(evt)
        return

    intent, flights = cached

    yield _event({"type": "refine_started", "refinement": refinement, "change_type": change_type})

    filtered = _filter_flights(flights, change_type)

    try:
        _, optimizer = _build_agents(profile)
    except Exception as exc:
        yield _event({"type": "error", "message": str(exc)})
        return

    yield _event({"type": "optimizer_started"})

    state = RequestState(raw_input=refinement, intent=intent, flight_options=filtered)
    try:
        state = await optimizer.run(state)
    except Exception as exc:
        yield _event({"type": "error", "message": f"Optimizer failed: {exc}"})
        return

    if not state.archetypes:
        yield _event({"type": "error", "message": "No options found after applying filter."})
        return

    # Update cache with filtered flights so subsequent refines stack
    search_cache.put(request_id, intent, filtered)

    for archetype in state.archetypes:
        yield _event({"type": "archetype_ready", "archetype": archetype.model_dump(mode="json")})

    yield _event({"type": "done", "request_id": request_id})


@router.post("/refine")
async def refine(body: RefineRequest, request: Request) -> StreamingResponse:
    llm_profile = getattr(request.state, "llm_profile", None)
    profile = _resolve_profile(llm_profile)
    return StreamingResponse(
        _refine_generator(body.request_id, body.refinement, profile),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
