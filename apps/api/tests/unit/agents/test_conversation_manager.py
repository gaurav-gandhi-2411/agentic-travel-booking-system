"""Unit tests for ConversationManagerAgent — all LLM calls use a mock client.

The boundary:
  - Unit tests (here): verify the agent handles LLM responses correctly,
    including happy-path parsing and all three fallback modes.
  - Evals (evals/conversation_manager/): verify the LLM produces the right
    responses across the 15 scenario dataset.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from travel_agent.agents.conversation_manager import ConversationManagerAgent
from travel_agent.agents.conversation_manager_types import (
    ConversationAction,
    ConversationManagerOutput,
    RefineArgs,
    ReplanArgs,
)
from travel_agent.coordinator.state import (
    Archetype,
    ArchetypeLabel,
    CabinClass,
    FlightOption,
    RequestState,
    TravelIntent,
    Window,
)
from travel_agent.llm.base import LLMClient, LLMResponse, ToolCall

# ── helpers ───────────────────────────────────────────────────────────────────

_MODEL = "llama-3.3-70b-versatile"


def _mock_client(tool_call_input: dict[str, Any] | None = None, *, free_text: str = "") -> LLMClient:
    """Return a mock LLMClient with a configurable response."""
    response = LLMResponse(
        content=free_text,
        model=_MODEL,
        input_tokens=300,
        output_tokens=60,
        latency_ms=800.0,
        tool_calls=(
            [ToolCall(name="extract_conversation_action", input=tool_call_input or {}, id="tc-001")]
            if tool_call_input is not None
            else []
        ),
    )
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=response)
    return client  # type: ignore[return-value]


def _agent(client: LLMClient | None = None, **kwargs: Any) -> ConversationManagerAgent:
    if client is None:
        client = _mock_client({"action": "no_op", "no_op_args": {"explanation": "I help with flights."}})
    return ConversationManagerAgent(client, _MODEL, **kwargs)


def _minimal_state() -> RequestState:
    return RequestState(raw_input="show me something cheaper")


def _full_state() -> RequestState:
    """State with intent, flight pool, and archetypes."""
    intent = TravelIntent(
        origin_iata="DEL",
        destination_iata="DXB",
        earliest_departure=date(2026, 12, 1),
        latest_departure=date(2026, 12, 31),
        budget_inr=30000,
    )
    window = Window(start_date=date(2026, 12, 10), end_date=date(2026, 12, 17))
    flights = [
        FlightOption(
            window=window,
            provider="synthetic",
            origin_iata="DEL",
            destination_iata="DXB",
            outbound_departure_at="2026-12-10T08:00:00",
            outbound_arrival_at="2026-12-10T11:45:00",
            airline_code="EK",
            flight_number="EK-511",
            cabin_class=CabinClass.ECONOMY,
            price_inr=p,
            outbound_duration_minutes=225,
            layover_count=s,
            is_refundable=False,
        )
        for p, s in [(18500, 1), (28000, 0), (35000, 0), (45000, 0)]
    ]
    arch_flight = flights[0]
    archetypes = [
        Archetype(
            label=ArchetypeLabel.BEST_VALUE,
            flight=arch_flight,
            explanation="Cheapest option",
            deeplink_url="https://example.com",
        )
    ]
    state = RequestState(
        raw_input="show me something cheaper",
        intent=intent,
        flight_options=flights,
        archetypes=archetypes,
    )
    return state


# ── happy path — REFINE ───────────────────────────────────────────────────────


async def test_refine_action_parsed_correctly() -> None:
    tool_input = {
        "action": "refine",
        "refine_args": {"direct_only": True, "sort_by": "price"},
    }
    agent = _agent(_mock_client(tool_input))
    out = await agent.understand("only direct flights", _minimal_state())

    assert out.action == ConversationAction.REFINE
    assert out.refine_args is not None
    assert out.refine_args.direct_only is True
    assert out.refine_args.sort_by == "price"
    assert out.replan_args is None
    assert out.no_op_args is None


async def test_refine_price_max_parsed() -> None:
    tool_input = {
        "action": "refine",
        "refine_args": {"price_max_inr": 20000, "sort_by": "price"},
    }
    agent = _agent(_mock_client(tool_input))
    out = await agent.understand("under 20000 rupees", _minimal_state())

    assert out.action == ConversationAction.REFINE
    assert out.refine_args is not None
    assert out.refine_args.price_max_inr == 20000


async def test_refine_departure_window_parsed() -> None:
    tool_input = {
        "action": "refine",
        "refine_args": {"departure_window": "morning", "sort_by": "price"},
    }
    agent = _agent(_mock_client(tool_input))
    out = await agent.understand("morning flights only", _minimal_state())

    assert out.refine_args is not None
    assert out.refine_args.departure_window == "morning"


# ── happy path — REPLAN ───────────────────────────────────────────────────────


async def test_replan_action_parsed_correctly() -> None:
    tool_input = {
        "action": "replan",
        "replan_args": {"destination_iata": "SIN"},
    }
    agent = _agent(_mock_client(tool_input))
    out = await agent.understand("actually try Singapore", _minimal_state())

    assert out.action == ConversationAction.REPLAN
    assert out.replan_args is not None
    assert out.replan_args.destination_iata == "SIN"
    assert out.refine_args is None
    assert out.no_op_args is None


async def test_replan_date_change_parsed() -> None:
    tool_input = {
        "action": "replan",
        "replan_args": {
            "departure_window_start": "2026-11-01",
            "departure_window_end": "2026-11-30",
        },
    }
    agent = _agent(_mock_client(tool_input))
    out = await agent.understand("try November instead", _minimal_state())

    assert out.action == ConversationAction.REPLAN
    assert out.replan_args is not None
    assert out.replan_args.departure_window_start == date(2026, 11, 1)


# ── happy path — NO_OP ───────────────────────────────────────────────────────


async def test_no_op_action_parsed_correctly() -> None:
    explanation = "I help refine flight searches. Want to filter options?"
    tool_input = {
        "action": "no_op",
        "no_op_args": {"explanation": explanation},
    }
    agent = _agent(_mock_client(tool_input))
    out = await agent.understand("what's the weather in Dubai?", _minimal_state())

    assert out.action == ConversationAction.NO_OP
    assert out.no_op_args is not None
    assert out.no_op_args.explanation == explanation
    assert out.refine_args is None
    assert out.replan_args is None


async def test_no_op_intentional_logs_correct_event() -> None:
    explanation = "I help refine flight searches. Want to filter options?"
    tool_input = {
        "action": "no_op",
        "no_op_args": {"explanation": explanation},
    }
    agent = _agent(_mock_client(tool_input))

    with structlog.testing.capture_logs() as cap_logs:
        await agent.understand("tell me a joke", _minimal_state())

    events = [log["event"] for log in cap_logs]
    assert "conversation_manager_classified_no_op" in events


# ── fallback: no tool call ────────────────────────────────────────────────────


async def test_no_tool_call_falls_back_to_no_op() -> None:
    client = _mock_client(free_text="I can help you with flights!")
    # tool_call_input=None → response.tool_calls=[]
    agent = _agent(client)
    out = await agent.understand("hello", _minimal_state())

    assert out.action == ConversationAction.NO_OP
    assert out.no_op_args is not None
    assert len(out.no_op_args.explanation) >= 20


async def test_no_tool_call_logs_no_tool_call_event() -> None:
    client = _mock_client(free_text="Sure, let me help you!")
    agent = _agent(client)

    with structlog.testing.capture_logs() as cap_logs:
        await agent.understand("hi", _minimal_state())

    events = [log["event"] for log in cap_logs]
    assert "conversation_manager_no_tool_call" in events


# ── fallback: malformed tool call ────────────────────────────────────────────


async def test_malformed_tool_call_falls_back_to_no_op() -> None:
    # Tool call with two args populated — violates exactly-one-args invariant
    bad_input = {
        "action": "refine",
        "refine_args": {"sort_by": "price"},
        "replan_args": {"destination_iata": "SIN"},
    }
    agent = _agent(_mock_client(bad_input))
    out = await agent.understand("something ambiguous", _minimal_state())

    assert out.action == ConversationAction.NO_OP
    assert out.no_op_args is not None


async def test_malformed_tool_call_logs_parse_failed_event() -> None:
    bad_input = {
        "action": "refine",
        "refine_args": {"sort_by": "price"},
        "replan_args": {"destination_iata": "SIN"},
    }
    agent = _agent(_mock_client(bad_input))

    with structlog.testing.capture_logs() as cap_logs:
        await agent.understand("something ambiguous", _minimal_state())

    events = [log["event"] for log in cap_logs]
    assert "conversation_manager_parse_failed" in events


async def test_completely_invalid_tool_output_falls_back() -> None:
    # action field missing entirely
    bad_input: dict[str, Any] = {"refine_args": {"sort_by": "price"}}
    agent = _agent(_mock_client(bad_input))
    out = await agent.understand("filter", _minimal_state())

    assert out.action == ConversationAction.NO_OP


# ── context building ──────────────────────────────────────────────────────────


async def test_understand_with_full_state_calls_llm() -> None:
    """Agent calls LLM even when state has full intent+flights+archetypes."""
    tool_input = {"action": "refine", "refine_args": {"sort_by": "price"}}
    client = _mock_client(tool_input)
    agent = _agent(client)

    out = await agent.understand("cheaper please", _full_state())
    assert out.action == ConversationAction.REFINE
    client.chat.assert_called_once()


async def test_understand_with_empty_state_does_not_crash() -> None:
    """Empty RequestState (no intent, no flights) is handled gracefully."""
    tool_input = {"action": "no_op", "no_op_args": {"explanation": "I help refine searches here."}}
    agent = _agent(_mock_client(tool_input))
    out = await agent.understand("hi", RequestState())

    assert out.action == ConversationAction.NO_OP


# ── extra_params threading ────────────────────────────────────────────────────


async def test_extra_params_passed_through_to_llm() -> None:
    """extra_params from profile config reach the LLM client chat() call."""
    tool_input = {"action": "refine", "refine_args": {"sort_by": "price"}}
    client = _mock_client(tool_input)
    agent = ConversationManagerAgent(
        client, _MODEL, extra_params={"reasoning_effort": "low"}
    )
    await agent.understand("cheaper", _minimal_state())

    _, kwargs = client.chat.call_args
    assert kwargs.get("extra_params") == {"reasoning_effort": "low"}


# ── tool definition wired correctly ──────────────────────────────────────────


async def test_agent_passes_correct_tool_to_llm() -> None:
    tool_input = {"action": "refine", "refine_args": {"sort_by": "price"}}
    client = _mock_client(tool_input)
    agent = _agent(client)
    await agent.understand("make it cheaper", _minimal_state())

    _, kwargs = client.chat.call_args
    assert kwargs.get("tools") is not None
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0].name == "extract_conversation_action"


async def test_agent_uses_zero_temperature() -> None:
    tool_input = {"action": "refine", "refine_args": {"sort_by": "price"}}
    client = _mock_client(tool_input)
    agent = _agent(client)
    await agent.understand("cheaper", _minimal_state())

    _, kwargs = client.chat.call_args
    assert kwargs.get("temperature") == 0.0
