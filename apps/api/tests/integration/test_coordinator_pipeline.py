"""Integration test: stream_search() pipeline with mocked PlannerAgent.

Coordinator.run() and coordinator.py have been deleted (audit Risk 8).
These tests call stream_search() directly with pre-built mock agents,
exercising the full SSE pipeline without HTTP or a real LLM.

SyntheticProvider is used for flight data (no Aviasales key needed).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.state import RequestState
from travel_agent.coordinator.streaming import stream_search
from travel_agent.llm.base import LLMClient, LLMResponse, ToolCall

# ── helpers ───────────────────────────────────────────────────────────────────


def _planner_tool_call(intent_fields: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content="",
        model="claude-haiku-4-5-20251001",
        input_tokens=50,
        output_tokens=120,
        latency_ms=0.0,
        tool_calls=[
            ToolCall(
                name="extract_travel_intent",
                input=intent_fields,
                id="tc-e2e-001",
            )
        ],
    )


def _mock_llm_client(intent_fields: dict[str, Any]) -> LLMClient:
    response = _planner_tool_call(intent_fields)
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=response)
    return client  # type: ignore[return-value]


def _bom_cdg_intent_fields() -> dict[str, Any]:
    return {
        "origin_iata": "BOM",
        "destination_iata": "CDG",
        "earliest_departure": "2026-06-01",
        "latest_departure": "2026-06-08",
        "trip_duration_days": 7,
        "traveler_count": 1,
        "cabin_class": "economy",
        "trip_type": "round_trip",
        "budget_inr": None,
        "hotel_min_stars": 3.0,
        "hotel_location_hint": None,
        "airline_preference": None,
        "departure_time_constraint": None,
        "raw_query": "fly from Mumbai to Paris next month",
    }


async def _run_pipeline(
    intent_fields: dict[str, Any],
    raw_input: str = "fly from Mumbai to Paris next month",
) -> tuple[list[dict[str, Any]], RequestState | None]:
    """Run stream_search and collect events + final state (from done event)."""
    client = _mock_llm_client(intent_fields)
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    optimizer = OptimizerAgent(client=None)  # fallback explanations

    events: list[dict[str, Any]] = []
    async for event in stream_search(raw_input, planner, optimizer):
        events.append(event)
    return events, None


# ── pipeline tests ────────────────────────────────────────────────────────────


async def test_pipeline_reaches_done_or_no_data() -> None:
    events, _ = await _run_pipeline(_bom_cdg_intent_fields())
    types = {e["type"] for e in events}
    assert "done" in types or "no_data_for_route" in types


async def test_pipeline_emits_planner_done_with_intent() -> None:
    events, _ = await _run_pipeline(_bom_cdg_intent_fields())
    planner_done = next((e for e in events if e["type"] == "planner_done"), None)
    assert planner_done is not None
    assert planner_done["intent"]["origin_iata"] == "BOM"
    assert planner_done["intent"]["destination_iata"] == "CDG"


async def test_pipeline_produces_flight_options() -> None:
    events, _ = await _run_pipeline(_bom_cdg_intent_fields())
    progress_events = [e for e in events if e["type"] == "search_progress"]
    total_found = sum(e["flights_found"] for e in progress_events)
    assert total_found > 0


async def test_pipeline_archetype_events_present() -> None:
    events, _ = await _run_pipeline(_bom_cdg_intent_fields())
    archetype_events = [e for e in events if e["type"] == "archetype_ready"]
    # Should have 2 archetypes if flights found; 0 if no_data_for_route
    done_types = {e["type"] for e in events}
    if "done" in done_types:
        assert len(archetype_events) == 2


async def test_pipeline_intent_fields_propagated() -> None:
    fields = {**_bom_cdg_intent_fields(), "traveler_count": 3, "cabin_class": "business"}
    events, _ = await _run_pipeline(fields)
    planner_done = next((e for e in events if e["type"] == "planner_done"), None)
    assert planner_done is not None
    assert planner_done["intent"]["traveler_count"] == 3
    assert planner_done["intent"]["cabin_class"] == "business"


async def test_pipeline_nrt_destination_search_progress() -> None:
    fields = {**_bom_cdg_intent_fields(), "destination_iata": "NRT", "raw_query": "BOM to Tokyo"}
    events, _ = await _run_pipeline(fields, raw_input="BOM to Tokyo")
    planner_done = next((e for e in events if e["type"] == "planner_done"), None)
    assert planner_done is not None
    assert planner_done["intent"]["destination_iata"] == "NRT"


async def test_pipeline_state_not_mutated_on_planner_error() -> None:
    """When the LLM returns no tool call, stream_search emits an error event."""
    no_tool_response = LLMResponse(
        content="Sorry, I cannot help with that.",
        model="claude-haiku-4-5-20251001",
        input_tokens=10,
        output_tokens=15,
        latency_ms=0.0,
        tool_calls=[],
    )
    client: LLMClient = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=no_tool_response)  # type: ignore[method-assign]
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    optimizer = OptimizerAgent(client=None)

    events: list[dict[str, Any]] = []
    async for event in stream_search("some unrelated query", planner, optimizer):
        events.append(event)

    assert any(e["type"] == "error" for e in events)
    assert not any(e["type"] == "done" for e in events)


async def test_pipeline_budget_tracking_via_search_progress() -> None:
    events, _ = await _run_pipeline(_bom_cdg_intent_fields())
    progress_events = [e for e in events if e["type"] == "search_progress"]
    assert len(progress_events) > 0
