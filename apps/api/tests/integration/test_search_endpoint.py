"""Integration test: POST /search SSE endpoint.

Uses a mocked PlannerAgent and SyntheticProvider (no real API calls).
Verifies the full SSE event sequence is emitted correctly:
  planner_started → planner_done → search_started → search_progress (x N)
  → search_done → optimizer_started → archetype_ready (x2) → done
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from travel_agent.api.main import app
from travel_agent.coordinator.state import (
    CoordinatorPhase,
    RequestState,
    TravelIntent,
)
from travel_agent.llm.base import LLMClient, LLMResponse, ToolCall

# ── helpers ───────────────────────────────────────────────────────────────────

_NEXT_MONTH = "2026-06-01"
_NEXT_MONTH_END = "2026-06-30"


def _intent_fields() -> dict[str, Any]:
    return {
        "origin_iata": "BOM",
        "destination_iata": "CDG",
        "earliest_departure": _NEXT_MONTH,
        "latest_departure": _NEXT_MONTH_END,
        "trip_duration_days": 5,
        "traveler_count": 1,
        "cabin_class": "economy",
        "trip_type": "round_trip",
        "raw_query": "BOM to CDG next month next month",
    }


def _make_planner_response(intent_fields: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content="",
        model="claude-haiku-4-5-20251001",
        input_tokens=50,
        output_tokens=100,
        latency_ms=0.0,
        tool_calls=[ToolCall(name="extract_travel_intent", input=intent_fields, id="tc-sse-001")],
    )


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    """Parse raw SSE text into a list of event dicts."""
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            events.append(json.loads(payload))
    return events


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def mock_llm() -> LLMClient:
    response = _make_planner_response(_intent_fields())
    llm = AsyncMock(spec=LLMClient)
    llm.chat = AsyncMock(return_value=response)
    return llm  # type: ignore[return-value]


# ── tests ─────────────────────────────────────────────────────────────────────


def test_search_returns_event_stream_content_type(client: TestClient, mock_llm: LLMClient) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


def test_search_emits_planner_started(client: TestClient, mock_llm: LLMClient) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "planner_started" in types


def test_search_emits_full_event_sequence(client: TestClient, mock_llm: LLMClient) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    assert types[0] == "planner_started"
    assert "planner_done" in types
    assert "search_started" in types
    assert "search_done" in types
    assert "optimizer_started" in types
    assert types[-1] == "done"


def test_search_emits_two_archetype_ready_events(client: TestClient, mock_llm: LLMClient) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    events = _parse_sse(resp.text)
    archetype_events = [e for e in events if e["type"] == "archetype_ready"]
    assert len(archetype_events) == 2


def test_search_archetype_labels(client: TestClient, mock_llm: LLMClient) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    events = _parse_sse(resp.text)
    archetype_events = [e for e in events if e["type"] == "archetype_ready"]
    labels = {e["archetype"]["label"] for e in archetype_events}
    assert labels == {"best-value", "best-experience"}


def test_search_archetype_has_flight_and_explanation(
    client: TestClient, mock_llm: LLMClient
) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    events = _parse_sse(resp.text)
    for event in events:
        if event["type"] == "archetype_ready":
            arch = event["archetype"]
            assert "flight" in arch
            assert "explanation" in arch
            assert len(arch["explanation"]) > 0


def test_search_planner_done_contains_intent(client: TestClient, mock_llm: LLMClient) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    events = _parse_sse(resp.text)
    planner_done = next(e for e in events if e["type"] == "planner_done")
    assert "intent" in planner_done
    assert planner_done["intent"]["origin_iata"] == "BOM"
    assert planner_done["intent"]["destination_iata"] == "CDG"


def test_search_progress_events_sum_to_total(client: TestClient, mock_llm: LLMClient) -> None:
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    events = _parse_sse(resp.text)
    progress_events = [e for e in events if e["type"] == "search_progress"]
    search_done = next(e for e in events if e["type"] == "search_done")

    total_from_progress = sum(e["flights_found"] for e in progress_events)
    assert total_from_progress == search_done["total_options"]


def test_search_without_auth_passes_in_synthetic_mode(client: TestClient, mock_llm: LLMClient) -> None:
    """APP_MODE=synthetic (default) — no auth header required."""
    with patch("travel_agent.api.routes.search._build_agents") as mock_build:
        from travel_agent.agents.optimizer import OptimizerAgent
        from travel_agent.agents.planner import PlannerAgent

        planner = PlannerAgent(mock_llm, "claude-haiku-4-5-20251001")
        optimizer = OptimizerAgent(client=None)
        mock_build.return_value = (planner, optimizer)

        # No X-API-Key header, APP_MODE defaults to synthetic
        resp = client.post("/search", json={"query": "BOM to CDG next month next month"})

    assert resp.status_code == 200
