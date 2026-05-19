"""POST /refine — LLM-driven conversation handler (Phase 2C.4 PR 2/3).

Classifies the user message via ConversationManagerAgent, then dispatches:

  REFINE  — apply RefineArgs filter spec to cached flight pool, re-run
             OptimizerAgent, stream updated archetypes.
  REPLAN  — merge ReplanArgs into cached intent, re-run full flight-search
             pipeline via stream_replan (PlannerAgent skipped).
  NO_OP   — emit conversation_message SSE with the agent's explanation text.

Event sequence:
  conversation_thinking
  conversation_action_classified  {action, args_summary, args}
  [REFINE path]
    optimizer_started
    archetype_ready               {archetype}   (x2)
    done                          {request_id}
  [REPLAN path]
    search_started                {windows}
    search_progress               {window_idx, flights_found}  (one per month)
    search_done                   {total_options}
    optimizer_started
    archetype_ready               {archetype}   (x2)
    done                          {request_id}
  [NO_OP path]
    conversation_message          {text}
  error                           {message}     (on failure)
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from travel_agent.agents.conversation_manager import ConversationManagerAgent
from travel_agent.agents.conversation_manager_types import (
    ConversationAction,
    RefineArgs,
    ReplanArgs,
)
from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.api.cache import search_cache
from travel_agent.coordinator.state import FlightOption, RequestState, TravelIntent
from travel_agent.coordinator.streaming import StreamEventType, stream_replan
from travel_agent.llm import get_llm_client_and_model
from travel_agent.observability.langfuse_client import get_langfuse, set_request_trace

router = APIRouter()

_ALLOWED_PROFILES: frozenset[str] = frozenset({"demo-haiku", "demo-llama", "demo-gpt-oss-120b"})

# Departure-window hour boundaries (24h clock, inclusive start, exclusive end)
_DEP_HOUR_MORNING_START = 6  # 06:00
_DEP_HOUR_NOON = 12  # 12:00
_DEP_HOUR_AFTERNOON_END = 17  # 17:00
_DEP_HOUR_EVENING_END = 21  # 21:00

_EMPTY_POOL_TEXT = (
    "No flights match those filters. Want to try different criteria or start a new search?"
)


def _resolve_profile(requested: str | None) -> str:
    if requested in _ALLOWED_PROFILES:
        return requested
    env = os.environ.get("LLM_ROUTING_PROFILE", "demo")
    return "demo-haiku" if env == "demo" else env


class RefineRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    refinement: str = Field(min_length=1, max_length=1000)


def _apply_refine_filters(flights: list[FlightOption], args: RefineArgs) -> list[FlightOption]:
    """Apply RefineArgs filter spec to the pool. Returns empty list when no flight matches."""
    if args.clear_filters:
        return list(flights)

    pool = list(flights)

    if args.price_max_inr is not None:
        pool = [f for f in pool if f.price_inr <= args.price_max_inr]

    if args.price_min_inr is not None:
        pool = [f for f in pool if f.price_inr >= args.price_min_inr]

    if args.direct_only:
        pool = [f for f in pool if f.layover_count == 0]
    elif args.max_layover_count is not None:
        pool = [f for f in pool if f.layover_count <= args.max_layover_count]

    if args.departure_window is not None:

        def _hour(iso: str) -> int:
            try:
                return int(iso[11:13])
            except (ValueError, IndexError):
                return 12

        if args.departure_window == "morning":
            pool = [
                f
                for f in pool
                if _DEP_HOUR_MORNING_START <= _hour(f.outbound_departure_at) < _DEP_HOUR_NOON
            ]
        elif args.departure_window == "afternoon":
            pool = [
                f
                for f in pool
                if _DEP_HOUR_NOON <= _hour(f.outbound_departure_at) < _DEP_HOUR_AFTERNOON_END
            ]
        elif args.departure_window == "evening":
            pool = [
                f
                for f in pool
                if _DEP_HOUR_AFTERNOON_END <= _hour(f.outbound_departure_at) < _DEP_HOUR_EVENING_END
            ]
        elif args.departure_window == "night":
            pool = [
                f
                for f in pool
                if not (
                    _DEP_HOUR_MORNING_START
                    <= _hour(f.outbound_departure_at)
                    < _DEP_HOUR_EVENING_END
                )
            ]

    sort_key = {
        "price": lambda f: f.price_inr,
        "duration": lambda f: f.outbound_duration_minutes,
        "stops": lambda f: f.layover_count,
    }[args.sort_by]
    return sorted(pool, key=sort_key)


def _merge_replan_intent(base: TravelIntent, args: ReplanArgs) -> TravelIntent:
    """Return a copy of base with non-null ReplanArgs fields merged in."""
    updates: dict[str, object] = {}
    if args.origin_iata is not None:
        updates["origin_iata"] = args.origin_iata
    if args.destination_iata is not None:
        updates["destination_iata"] = args.destination_iata
    if args.departure_window_start is not None:
        updates["earliest_departure"] = args.departure_window_start
    if args.departure_window_end is not None:
        updates["latest_departure"] = args.departure_window_end
    if args.budget_max_inr is not None:
        updates["budget_inr"] = args.budget_max_inr
    if args.preferred_airlines:
        updates["airline_preference"] = ", ".join(args.preferred_airlines)
    return base.model_copy(update=updates)


async def _refine_generator(  # noqa: PLR0911, PLR0912, PLR0915
    request_id: str,
    refinement: str,
    profile: str,
) -> AsyncGenerator[str, None]:
    def _event(data: dict[str, object]) -> str:
        return f"data: {json.dumps(data)}\n\n"

    lf = get_langfuse()
    trace = None
    with contextlib.suppress(Exception):
        if lf is not None:
            trace = lf.start_observation(
                name="refine",
                as_type="span",
                input={"refinement": refinement, "profile": profile},
                metadata={"request_id": request_id, "session_id": request_id},
            )
            set_request_trace(trace)

    yield _event({"type": StreamEventType.CONVERSATION_THINKING})

    cached = await search_cache.get(request_id)
    if cached is None:
        yield _event(
            {
                "type": StreamEventType.ERROR,
                "message": "Session expired. Please start a new search.",
            }
        )
        return

    intent, flights = cached
    state = RequestState(raw_input=refinement, intent=intent, flight_options=flights)

    try:
        conv_client, conv_model = get_llm_client_and_model("conversation", profile)
    except Exception as exc:
        yield _event({"type": StreamEventType.ERROR, "message": str(exc)})
        return

    conv_agent = ConversationManagerAgent(client=conv_client, model=conv_model)
    try:
        classification = await conv_agent.understand(refinement, state)
    except Exception as exc:
        yield _event(
            {
                "type": StreamEventType.ERROR,
                "message": f"Classification failed: {exc}",
            }
        )
        return

    if classification.action == ConversationAction.REFINE and classification.refine_args:
        args_dict = classification.refine_args.model_dump(exclude_none=True, mode="json")
    elif classification.action == ConversationAction.REPLAN and classification.replan_args:
        args_dict = classification.replan_args.model_dump(exclude_none=True, mode="json")
    else:
        args_dict = {}

    yield _event(
        {
            "type": StreamEventType.CONVERSATION_ACTION_CLASSIFIED,
            "action": classification.action,
            "args_summary": classification.args_summary,
            "args": args_dict,
        }
    )

    if classification.action == ConversationAction.REFINE:
        assert classification.refine_args is not None  # noqa: S101 — invariant from model_validator
        filtered = _apply_refine_filters(flights, classification.refine_args)

        if not filtered:
            yield _event(
                {
                    "type": StreamEventType.CONVERSATION_MESSAGE,
                    "text": _EMPTY_POOL_TEXT,
                }
            )
            return

        try:
            optimizer_client, optimizer_model = get_llm_client_and_model("optimizer", profile)
            optimizer = OptimizerAgent(
                client=optimizer_client,
                model=optimizer_model,
                partner_marker=os.environ.get("AVIASALES_PARTNER_ID", ""),
            )
        except Exception as exc:
            yield _event({"type": StreamEventType.ERROR, "message": str(exc)})
            return

        yield _event({"type": StreamEventType.OPTIMIZER_STARTED})
        refine_state = RequestState(raw_input=refinement, intent=intent, flight_options=filtered)
        try:
            refine_state = await optimizer.run(refine_state)
        except Exception as exc:
            yield _event(
                {
                    "type": StreamEventType.ERROR,
                    "message": f"Optimizer failed: {exc}",
                }
            )
            return

        if not refine_state.archetypes:
            yield _event(
                {
                    "type": StreamEventType.CONVERSATION_MESSAGE,
                    "text": _EMPTY_POOL_TEXT,
                }
            )
            return

        await search_cache.put(request_id, intent, filtered)

        for archetype in refine_state.archetypes:
            yield _event(
                {
                    "type": StreamEventType.ARCHETYPE_READY,
                    "archetype": archetype.model_dump(mode="json"),
                }
            )

        yield _event({"type": StreamEventType.DONE, "request_id": request_id})

    elif classification.action == ConversationAction.REPLAN:
        assert classification.replan_args is not None  # noqa: S101 — invariant from model_validator
        new_intent = _merge_replan_intent(intent, classification.replan_args)

        try:
            optimizer_client, optimizer_model = get_llm_client_and_model("optimizer", profile)
            optimizer = OptimizerAgent(
                client=optimizer_client,
                model=optimizer_model,
                partner_marker=os.environ.get("AVIASALES_PARTNER_ID", ""),
            )
        except Exception as exc:
            yield _event({"type": StreamEventType.ERROR, "message": str(exc)})
            return

        async for evt in stream_replan(new_intent, optimizer):
            yield _event(evt)

    else:  # NO_OP
        assert classification.no_op_args is not None  # noqa: S101 — invariant from model_validator
        yield _event(
            {
                "type": StreamEventType.CONVERSATION_MESSAGE,
                "text": classification.no_op_args.explanation,
            }
        )

    with contextlib.suppress(Exception):
        if lf is not None and trace is not None:
            trace.end()
            lf.flush()


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
