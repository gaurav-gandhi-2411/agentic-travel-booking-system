"""Integration test: coordinator -> PlannerAgent -> window search -> FlightHunter + HotelHunter.

Uses a mocked LLMClient (so no real API calls) and SyntheticProvider (no Aviasales key needed).
Verifies the full pipeline produces a terminal PRESENTING state with flight and hotel options.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.coordinator import Coordinator
from travel_agent.coordinator.state import (
    CabinClass,
    CoordinatorPhase,
    RequestState,
    TravelIntent,
    TripType,
)
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
    return client


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


# ── full pipeline: PlannerAgent + Coordinator ─────────────────────────────────


async def test_pipeline_reaches_presenting_phase() -> None:
    client = _mock_llm_client(_bom_cdg_intent_fields())
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    coordinator = Coordinator()

    state = RequestState()
    state.raw_input = "fly from Mumbai to Paris next month"

    state = await planner.run(state, today=date(2026, 5, 14))
    assert state.phase != CoordinatorPhase.ERROR, state.errors
    assert state.intent is not None

    result = await coordinator.run(state)
    assert result.phase == CoordinatorPhase.PRESENTING


async def test_pipeline_produces_flight_options() -> None:
    client = _mock_llm_client(_bom_cdg_intent_fields())
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    coordinator = Coordinator()

    state = RequestState()
    state.raw_input = "fly from Mumbai to Paris next month"
    state = await planner.run(state, today=date(2026, 5, 14))
    result = await coordinator.run(state)

    assert len(result.flight_options) > 0
    assert all(opt.origin_iata == "BOM" for opt in result.flight_options)
    assert all(opt.destination_iata == "CDG" for opt in result.flight_options)


async def test_pipeline_produces_hotel_options() -> None:
    client = _mock_llm_client(_bom_cdg_intent_fields())
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    coordinator = Coordinator()

    state = RequestState()
    state.raw_input = "fly from Mumbai to Paris next month"
    state = await planner.run(state, today=date(2026, 5, 14))
    result = await coordinator.run(state)

    assert len(result.hotel_options) > 0
    assert all(h.city == "Paris" for h in result.hotel_options)


async def test_pipeline_intent_fields_propagated() -> None:
    fields = {**_bom_cdg_intent_fields(), "traveler_count": 3, "cabin_class": "business"}
    client = _mock_llm_client(fields)
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    coordinator = Coordinator()

    state = RequestState()
    state.raw_input = "3 business class tickets BOM to CDG"
    state = await planner.run(state, today=date(2026, 5, 14))
    assert state.intent is not None
    assert state.intent.traveler_count == 3
    assert state.intent.cabin_class == CabinClass.BUSINESS

    result = await coordinator.run(state)
    assert result.phase == CoordinatorPhase.PRESENTING


async def test_pipeline_budget_tracking() -> None:
    client = _mock_llm_client(_bom_cdg_intent_fields())
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    coordinator = Coordinator()

    state = RequestState()
    state.raw_input = "fly from Mumbai to Paris next month"
    state = await planner.run(state, today=date(2026, 5, 14))
    result = await coordinator.run(state)

    assert result.call_budget.flight_calls_used > 0
    assert result.call_budget.hotel_calls_used > 0


async def test_pipeline_state_not_mutated_on_planner_error() -> None:
    no_tool_response = LLMResponse(
        content="Sorry, I cannot help with that.",
        model="claude-haiku-4-5-20251001",
        input_tokens=10,
        output_tokens=15,
        latency_ms=0.0,
        tool_calls=[],
    )
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=no_tool_response)
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")

    state = RequestState()
    state.raw_input = "some unrelated query"
    result = await planner.run(state, today=date(2026, 5, 14))

    assert result.phase == CoordinatorPhase.ERROR
    assert result.intent is None
    assert len(result.errors) == 1


async def test_pipeline_nrt_destination() -> None:
    fields = {**_bom_cdg_intent_fields(), "destination_iata": "NRT", "raw_query": "BOM to Tokyo"}
    client = _mock_llm_client(fields)
    planner = PlannerAgent(client, "claude-haiku-4-5-20251001")
    coordinator = Coordinator()

    state = RequestState()
    state.raw_input = "BOM to Tokyo"
    state = await planner.run(state, today=date(2026, 5, 14))
    result = await coordinator.run(state)

    assert result.phase == CoordinatorPhase.PRESENTING
    assert len(result.flight_options) > 0
    assert all(opt.destination_iata == "NRT" for opt in result.flight_options)
    assert all(h.city == "Tokyo" for h in result.hotel_options)


# ── Coordinator-only (pre-populated state) ────────────────────────────────────


async def test_coordinator_star_filter_respected() -> None:
    coordinator = Coordinator()

    state = RequestState()
    state.intent = TravelIntent(
        origin_iata="BOM",
        destination_iata="CDG",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 3),
        hotel_min_stars=4.0,
        trip_type=TripType.ROUND_TRIP,
        raw_query="Paris 4-star hotels",
    )
    result = await coordinator.run(state)
    assert all(h.stars >= 4.0 for h in result.hotel_options)


async def test_coordinator_empty_raw_input_still_works_with_intent() -> None:
    coordinator = Coordinator()

    state = RequestState()
    state.intent = TravelIntent(
        origin_iata="BOM",
        destination_iata="DPS",
        earliest_departure=date(2026, 8, 1),
        latest_departure=date(2026, 8, 3),
        trip_type=TripType.ROUND_TRIP,
        raw_query="Bali trip",
    )
    result = await coordinator.run(state)
    assert result.phase == CoordinatorPhase.PRESENTING
    assert len(result.flight_options) > 0
    assert len(result.hotel_options) > 0
