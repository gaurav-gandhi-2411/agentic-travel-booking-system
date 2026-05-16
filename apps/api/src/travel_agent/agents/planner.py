"""PlannerAgent — parses raw user text into a structured TravelIntent.

Calls the LLM with the extract_travel_intent tool and forces a tool-call
response.  The caller must pre-set state.raw_input before calling run().

Phase C: requires state.raw_input; sets state.intent on success.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import structlog

from travel_agent.agents.tools import EXTRACT_TRAVEL_INTENT
from travel_agent.coordinator.state import (
    CabinClass,
    CoordinatorPhase,
    RequestState,
    TravelIntent,
    TripType,
)
from travel_agent.llm.base import LLMClient, Message
from travel_agent.observability.langfuse_client import get_langfuse, get_request_trace
from travel_agent.observability.pricing import compute_cost

_logger = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "planner_system.txt"


def _load_system_prompt(today: date | None = None) -> str:
    template = _PROMPT_PATH.read_text()
    resolved_today = (today or datetime.now(tz=UTC).date()).isoformat()
    return template.replace("{today}", resolved_today)


class PlannerAgent:
    def __init__(self, client: LLMClient, model: str) -> None:
        self._client = client
        self._model = model

    async def run(
        self,
        state: RequestState,
        *,
        today: date | None = None,
    ) -> RequestState:
        if not state.raw_input:
            state.phase = CoordinatorPhase.ERROR
            state.errors.append("PlannerAgent requires non-empty state.raw_input")
            return state

        system = _load_system_prompt(today)
        messages = [Message(role="user", content=state.raw_input)]

        # cache_system_prompt=True enables prompt caching on AnthropicAdapter.
        # Note: haiku-4-5 requires ≥1024 tokens to be cache-eligible; the planner
        # system prompt is ~420 tokens so caching is a no-op for haiku profiles.
        # Caching activates for sonnet-4-6 (eval profile) where system prompts grow.
        # Non-Anthropic adapters accept **kwargs and safely ignore this kwarg.
        response = await self._client.chat(
            messages,
            model=self._model,
            max_tokens=1024,
            temperature=0.0,
            system=system,
            tools=[EXTRACT_TRAVEL_INTENT],
            cache_system_prompt=True,
        )

        # Cost telemetry + Langfuse generation — optional, never breaks the agent
        cost = compute_cost(
            response.model,
            response.input_tokens,
            response.output_tokens,
            response.cache_read_input_tokens,
            response.cache_creation_input_tokens,
        )
        _logger.info(
            "llm_call",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_input_tokens,
            cache_write_tokens=response.cache_creation_input_tokens,
            latency_ms=round(response.latency_ms, 1),
            cost_usd=cost,
        )
        with contextlib.suppress(Exception):
            trace = get_request_trace()
            if trace is not None:
                lf = get_langfuse()
                if lf is not None:
                    output: object = (
                        response.tool_calls[0].input if response.tool_calls else response.content
                    )
                    trace.start_observation(
                        name="planner_chat",
                        as_type="generation",
                        model=response.model,
                        input={"messages": [m.content for m in messages], "system": system[:200]},
                        output=output,
                        usage_details={
                            "input": response.input_tokens,
                            "output": response.output_tokens,
                        },
                        metadata={
                            "latency_ms": round(response.latency_ms, 1),
                            "adapter": type(self._client).__name__,
                            "cost_usd": cost,
                            "cache_read_tokens": response.cache_read_input_tokens,
                            "cache_write_tokens": response.cache_creation_input_tokens,
                        },
                    ).end()

        if not response.tool_calls:
            state.phase = CoordinatorPhase.ERROR
            state.errors.append(
                f"PlannerAgent: LLM returned no tool call (content={response.content!r})"
            )
            return state

        call = response.tool_calls[0]
        if call.name != EXTRACT_TRAVEL_INTENT.name:
            state.phase = CoordinatorPhase.ERROR
            state.errors.append(
                f"PlannerAgent: unexpected tool '{call.name}'; "
                f"expected '{EXTRACT_TRAVEL_INTENT.name}'"
            )
            return state

        try:
            intent = _parse_intent(call.input)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            state.phase = CoordinatorPhase.ERROR
            state.errors.append(f"PlannerAgent: failed to parse tool input: {exc}")
            return state

        state.intent = intent
        return state


def _parse_intent(raw: dict[str, object]) -> TravelIntent:
    budget = raw.get("budget_inr")
    return TravelIntent(
        origin_iata=str(raw["origin_iata"]),
        destination_iata=str(raw["destination_iata"]),
        earliest_departure=date.fromisoformat(str(raw["earliest_departure"])),
        latest_departure=date.fromisoformat(str(raw["latest_departure"])),
        trip_duration_days=_to_int(raw.get("trip_duration_days"), 7),
        traveler_count=_to_int(raw.get("traveler_count"), 1),
        cabin_class=CabinClass(str(raw.get("cabin_class", "economy"))),
        budget_inr=_to_int_opt(budget),
        hotel_min_stars=_to_float(raw.get("hotel_min_stars"), 3.0),
        hotel_location_hint=_optional_str(raw.get("hotel_location_hint")),
        trip_type=TripType(str(raw.get("trip_type", "round_trip"))),
        airline_preference=_optional_str(raw.get("airline_preference")),
        departure_time_constraint=_optional_str(raw.get("departure_time_constraint")),
        raw_query=str(raw["raw_query"]),
    )


def _to_int(val: object, default: int) -> int:
    if val is None:
        return default
    return int(str(val))


def _to_int_opt(val: object) -> int | None:
    if val is None:
        return None
    return int(str(val))


def _to_float(val: object, default: float) -> float:
    if val is None:
        return default
    return float(str(val))


def _optional_str(val: object) -> str | None:
    if val is None or val == "null":
        return None
    s = str(val).strip()
    return s if s else None
