"""Unit tests for PlannerAgent — all LLM calls use a mock client.

The boundary:
  - Unit tests (here): verify agent HANDLES LLM responses correctly.
  - Evals (evals/run.py): verify the LLM PRODUCES the right responses.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

from travel_agent.agents.planner import PlannerAgent, _load_system_prompt
from travel_agent.coordinator.state import (
    CabinClass,
    CoordinatorPhase,
    RequestState,
    TripType,
)
from travel_agent.llm.base import LLMClient, LLMResponse, ToolCall

# ── helpers ───────────────────────────────────────────────────────────────────


def _mock_client(tool_call_input: dict[str, Any] | None = None) -> LLMClient:
    """Return a mock LLMClient that simulates a successful tool-call response."""
    response = LLMResponse(
        content="",
        model="claude-haiku-4-5-20251001",
        input_tokens=400,
        output_tokens=80,
        latency_ms=500.0,
        tool_calls=(
            [ToolCall(name="extract_travel_intent", input=tool_call_input or {}, id="tc-001")]
            if tool_call_input is not None
            else []
        ),
    )
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=response)
    return client  # type: ignore[return-value]


def _baseline_tool_input(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "origin_iata": "BOM",
        "destination_iata": "CDG",
        "earliest_departure": "2026-06-01",
        "latest_departure": "2026-06-30",
        "trip_duration_days": 7,
        "traveler_count": 1,
        "cabin_class": "economy",
        "trip_type": "round_trip",
        "budget_inr": None,
        "hotel_min_stars": 3.0,
        "hotel_location_hint": None,
        "airline_preference": None,
        "departure_time_constraint": None,
        "raw_query": "fly BOM to CDG",
    }
    base.update(overrides)
    return base


# ── prompt loading ────────────────────────────────────────────────────────────


def test_load_system_prompt_injects_date() -> None:
    today = date(2026, 5, 14)
    prompt = _load_system_prompt(today)
    assert "2026-05-14" in prompt


def test_load_system_prompt_no_placeholder_remaining() -> None:
    today = date(2026, 5, 14)
    prompt = _load_system_prompt(today)
    assert "{today}" not in prompt


# ── happy path ────────────────────────────────────────────────────────────────


async def test_planner_sets_intent_on_success() -> None:
    client = _mock_client(_baseline_tool_input())
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="fly BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.origin_iata == "BOM"
    assert result.intent.destination_iata == "CDG"


async def test_planner_parses_dates_correctly() -> None:
    client = _mock_client(_baseline_tool_input())
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="fly BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.earliest_departure == date(2026, 6, 1)
    assert result.intent.latest_departure == date(2026, 6, 30)


async def test_planner_parses_cabin_class() -> None:
    client = _mock_client(_baseline_tool_input(cabin_class="business"))
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="business class BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.cabin_class == CabinClass.BUSINESS


async def test_planner_parses_traveler_count() -> None:
    client = _mock_client(_baseline_tool_input(traveler_count=4))
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="family of 4 BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.traveler_count == 4


async def test_planner_parses_budget() -> None:
    client = _mock_client(_baseline_tool_input(budget_inr=200000))
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="budget 2 lakhs BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.budget_inr == 200000


async def test_planner_parses_one_way() -> None:
    client = _mock_client(_baseline_tool_input(trip_type="one_way"))
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="one-way BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.trip_type == TripType.ONE_WAY


async def test_planner_parses_airline_preference() -> None:
    client = _mock_client(_baseline_tool_input(airline_preference="Air France"))
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="Air France BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.airline_preference == "Air France"


async def test_planner_parses_departure_constraint() -> None:
    client = _mock_client(_baseline_tool_input(departure_time_constraint="no red-eyes"))
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="no red-eye flights BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.departure_time_constraint == "no red-eyes"


async def test_planner_null_strings_become_none() -> None:
    client = _mock_client(_baseline_tool_input(airline_preference="null"))
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.intent is not None
    assert result.intent.airline_preference is None


# ── error cases ───────────────────────────────────────────────────────────────


async def test_planner_errors_on_empty_raw_input() -> None:
    client = _mock_client()
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.phase == CoordinatorPhase.ERROR
    assert result.errors
    client.chat.assert_not_called()


async def test_planner_errors_when_no_tool_call_returned() -> None:
    response = LLMResponse(
        content="Sorry, I cannot help with that.",
        model="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=20,
        latency_ms=300.0,
        tool_calls=[],
    )
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=response)
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")  # type: ignore[arg-type]
    state = RequestState(raw_input="fly BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.phase == CoordinatorPhase.ERROR
    assert result.intent is None


async def test_planner_errors_on_wrong_tool_name() -> None:
    response = LLMResponse(
        content="",
        model="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=20,
        latency_ms=300.0,
        tool_calls=[ToolCall(name="wrong_tool", input={}, id="tc-bad")],
    )
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=response)
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")  # type: ignore[arg-type]
    state = RequestState(raw_input="fly BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.phase == CoordinatorPhase.ERROR


async def test_planner_errors_on_bad_date_format() -> None:
    bad_input = _baseline_tool_input(earliest_departure="not-a-date")
    client = _mock_client(bad_input)
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="fly BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))

    assert result.phase == CoordinatorPhase.ERROR
    assert result.intent is None


async def test_planner_returns_state_with_intent() -> None:
    client = _mock_client(_baseline_tool_input())
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="fly BOM to CDG")
    result = await agent.run(state, today=date(2026, 5, 14))
    assert result.intent is not None
    assert result is state  # same object — agents mutate in-place


# ── llm call args ─────────────────────────────────────────────────────────────


async def test_planner_passes_tools_to_llm() -> None:
    client = _mock_client(_baseline_tool_input())
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="fly BOM to CDG")
    await agent.run(state, today=date(2026, 5, 14))

    _, kwargs = client.chat.call_args
    assert kwargs.get("tools") is not None
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0].name == "extract_travel_intent"


async def test_planner_passes_system_prompt() -> None:
    client = _mock_client(_baseline_tool_input())
    agent = PlannerAgent(client, "claude-haiku-4-5-20251001")
    state = RequestState(raw_input="fly BOM to CDG")
    await agent.run(state, today=date(2026, 5, 14))

    _, kwargs = client.chat.call_args
    assert kwargs.get("system") is not None
    assert "2026-05-14" in kwargs["system"]
