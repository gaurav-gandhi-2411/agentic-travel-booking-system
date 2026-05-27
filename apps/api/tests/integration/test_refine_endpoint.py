"""Integration tests: POST /refine SSE endpoint.

Uses a mocked SearchCache and mocked ConversationManagerAgent.understand so no
real LLM calls are made. Verifies the SSE event sequence for each dispatch path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from travel_agent.agents.conversation_manager import ConversationManagerAgent
from travel_agent.agents.conversation_manager_types import (
    ConversationAction,
    ConversationManagerOutput,
    NoOpArgs,
    RefineArgs,
    ReplanArgs,
)
from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.api.cache import search_cache
from travel_agent.api.main import app
from travel_agent.coordinator.state import (
    Archetype,
    ArchetypeLabel,
    CabinClass,
    FlightOption,
    RequestState,
    TravelIntent,
    Window,
)

_LLM_ROUTE = "travel_agent.api.routes.refine.get_llm_client_and_model"
_STREAM_REPLAN = "travel_agent.api.routes.refine.stream_replan"


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            events.append(json.loads(stripped[len("data:") :].strip()))
    return events


def _make_intent() -> TravelIntent:
    return TravelIntent(
        origin_iata="BOM",
        destination_iata="DEL",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 30),
    )


def _make_flights(n: int = 4) -> list[FlightOption]:
    window = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))
    return [
        FlightOption(
            window=window,
            provider="synthetic",
            origin_iata="BOM",
            destination_iata="DEL",
            outbound_departure_at="2026-06-01T08:00:00",
            outbound_arrival_at="2026-06-01T10:00:00",
            airline_code="XX",
            flight_number=f"XX-{i:03d}",
            cabin_class=CabinClass.ECONOMY,
            price_inr=10000 + i * 2000,
            outbound_duration_minutes=120,
            layover_count=0,
            is_refundable=False,
        )
        for i in range(n)
    ]


def _make_state_with_archetypes(intent: TravelIntent, flights: list[FlightOption]) -> RequestState:
    archetypes = [
        Archetype(
            label=ArchetypeLabel.BEST_VALUE,
            flight=flights[0],
            explanation="Cheapest option",
            deeplink_url="https://example.com",
        ),
        Archetype(
            label=ArchetypeLabel.BEST_EXPERIENCE,
            flight=flights[1],
            explanation="Best experience",
            deeplink_url="https://example.com",
        ),
    ]
    return RequestState(
        raw_input="direct flights",
        intent=intent,
        flight_options=flights,
        archetypes=archetypes,
    )


def _mock_client_model() -> tuple[AsyncMock, str]:
    return AsyncMock(), "test-model"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


_REQ_BODY = {"request_id": "test-req-001", "refinement": "only direct flights"}


# ── cache miss ────────────────────────────────────────────────────────────────


def test_refine_cache_miss_emits_error_event(client: TestClient) -> None:
    with patch.object(search_cache, "get", new=AsyncMock(return_value=None)):
        resp = client.post("/refine", json=_REQ_BODY)

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "error" in types
    error_evt = next(e for e in events if e["type"] == "error")
    assert "expired" in error_evt["message"].lower()


# ── event ordering ────────────────────────────────────────────────────────────


def test_refine_first_event_is_conversation_thinking(client: TestClient) -> None:
    intent, flights = _make_intent(), _make_flights()
    no_op = ConversationManagerOutput(
        action=ConversationAction.NO_OP,
        no_op_args=NoOpArgs(explanation="I only help with flight searches."),
    )
    with (
        patch.object(search_cache, "get", new=AsyncMock(return_value=(intent, flights))),
        patch(_LLM_ROUTE, return_value=_mock_client_model()),
        patch.object(ConversationManagerAgent, "understand", new=AsyncMock(return_value=no_op)),
    ):
        resp = client.post("/refine", json=_REQ_BODY)

    events = _parse_sse(resp.text)
    assert events[0]["type"] == "conversation_thinking"


# ── NO_OP path ────────────────────────────────────────────────────────────────


def test_refine_no_op_emits_conversation_message(client: TestClient) -> None:
    intent, flights = _make_intent(), _make_flights()
    no_op = ConversationManagerOutput(
        action=ConversationAction.NO_OP,
        no_op_args=NoOpArgs(explanation="I only help with flight searches, not hotels."),
    )
    with (
        patch.object(search_cache, "get", new=AsyncMock(return_value=(intent, flights))),
        patch(_LLM_ROUTE, return_value=_mock_client_model()),
        patch.object(ConversationManagerAgent, "understand", new=AsyncMock(return_value=no_op)),
    ):
        resp = client.post("/refine", json=_REQ_BODY)

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "conversation_message" in types
    msg = next(e for e in events if e["type"] == "conversation_message")
    assert len(msg["text"]) > 0
    assert events[-1]["type"] == "done"


# ── conversation_action_classified ────────────────────────────────────────────


def test_refine_action_classified_contains_args_summary(client: TestClient) -> None:
    intent, flights = _make_intent(), _make_flights()
    refine_output = ConversationManagerOutput(
        action=ConversationAction.REFINE,
        refine_args=RefineArgs(direct_only=True),
        args_summary="Direct flights only",
    )
    state = _make_state_with_archetypes(intent, flights)
    with (
        patch.object(search_cache, "get", new=AsyncMock(return_value=(intent, flights))),
        patch.object(search_cache, "put", new=AsyncMock()),
        patch(_LLM_ROUTE, return_value=_mock_client_model()),
        patch.object(
            ConversationManagerAgent,
            "understand",
            new=AsyncMock(return_value=refine_output),
        ),
        patch.object(OptimizerAgent, "run", new=AsyncMock(return_value=state)),
    ):
        resp = client.post("/refine", json=_REQ_BODY)

    events = _parse_sse(resp.text)
    classified = next((e for e in events if e["type"] == "conversation_action_classified"), None)
    assert classified is not None
    assert classified["args_summary"] == "Direct flights only"
    assert classified["action"] == "refine"


# ── REFINE happy path ─────────────────────────────────────────────────────────


def test_refine_refine_path_emits_archetypes_and_done(client: TestClient) -> None:
    intent, flights = _make_intent(), _make_flights()
    refine_output = ConversationManagerOutput(
        action=ConversationAction.REFINE,
        refine_args=RefineArgs(direct_only=True),
        args_summary="Direct flights only",
    )
    state = _make_state_with_archetypes(intent, flights)
    with (
        patch.object(search_cache, "get", new=AsyncMock(return_value=(intent, flights))),
        patch.object(search_cache, "put", new=AsyncMock()),
        patch(_LLM_ROUTE, return_value=_mock_client_model()),
        patch.object(
            ConversationManagerAgent,
            "understand",
            new=AsyncMock(return_value=refine_output),
        ),
        patch.object(OptimizerAgent, "run", new=AsyncMock(return_value=state)),
    ):
        resp = client.post("/refine", json=_REQ_BODY)

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "optimizer_started" in types
    assert len([e for e in events if e["type"] == "archetype_ready"]) == 2
    assert types[-1] == "done"


# ── REFINE empty pool ─────────────────────────────────────────────────────────


def test_refine_empty_pool_emits_conversation_message(client: TestClient) -> None:
    intent, flights = _make_intent(), _make_flights(4)
    # All flights priced 10000-16000; cap at 5000 yields empty pool
    refine_output = ConversationManagerOutput(
        action=ConversationAction.REFINE,
        refine_args=RefineArgs(price_max_inr=5000),
        args_summary="Under Rs. 5,000",
    )
    with (
        patch.object(search_cache, "get", new=AsyncMock(return_value=(intent, flights))),
        patch(_LLM_ROUTE, return_value=_mock_client_model()),
        patch.object(
            ConversationManagerAgent,
            "understand",
            new=AsyncMock(return_value=refine_output),
        ),
    ):
        resp = client.post("/refine", json=_REQ_BODY)

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "conversation_message" in types
    msg = next(e for e in events if e["type"] == "conversation_message")
    assert "No flights match" in msg["text"]
    assert events[-1]["type"] == "done"


# ── REPLAN path ───────────────────────────────────────────────────────────────


def test_refine_replan_path_emits_search_and_done(client: TestClient) -> None:
    intent, flights = _make_intent(), _make_flights()
    replan_output = ConversationManagerOutput(
        action=ConversationAction.REPLAN,
        replan_args=ReplanArgs(destination_iata="SIN"),
        args_summary="Searching BOM to SIN",
    )

    async def _fake_stream_replan(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "search_started", "windows": []}
        yield {"type": "search_done", "total_options": 4}
        yield {"type": "optimizer_started"}
        yield {
            "type": "archetype_ready",
            "archetype": {
                "label": "best-value",
                "flight": {},
                "explanation": "Cheapest",
                "deeplink_url": "https://example.com",
            },
        }
        yield {
            "type": "archetype_ready",
            "archetype": {
                "label": "best-experience",
                "flight": {},
                "explanation": "Best",
                "deeplink_url": "https://example.com",
            },
        }
        yield {"type": "done", "request_id": "new-req-001"}

    with (
        patch.object(search_cache, "get", new=AsyncMock(return_value=(intent, flights))),
        patch(_LLM_ROUTE, return_value=_mock_client_model()),
        patch.object(
            ConversationManagerAgent,
            "understand",
            new=AsyncMock(return_value=replan_output),
        ),
        patch(_STREAM_REPLAN, new=_fake_stream_replan),
    ):
        resp = client.post("/refine", json=_REQ_BODY)

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "search_started" in types
    assert "search_done" in types
    assert "optimizer_started" in types
    assert types[-1] == "done"
    done_evt = next(e for e in events if e["type"] == "done")
    assert done_evt["request_id"] == "new-req-001"


# ── request validation ────────────────────────────────────────────────────────


def test_refine_missing_request_id_returns_422(client: TestClient) -> None:
    resp = client.post("/refine", json={"refinement": "direct only"})
    assert resp.status_code == 422


def test_refine_refinement_too_long_returns_422(client: TestClient) -> None:
    resp = client.post("/refine", json={"request_id": "req-001", "refinement": "x" * 1001})
    assert resp.status_code == 422
