"""Unit tests for refine route helpers — pure functions and mocked generator paths.

Boundary:
  - Pure function tests (no mocking) cover _apply_refine_filters,
    _merge_replan_intent, and _resolve_profile.
  - Generator tests mock cache, LLM client, and agent to cover REFINE /
    REPLAN / NO_OP / cache-miss dispatch paths.
  - Integration / end-to-end SSE tests live in tests/integration/.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from travel_agent.agents.conversation_manager_types import (
    ConversationAction,
    ConversationManagerOutput,
    NoOpArgs,
    RefineArgs,
    ReplanArgs,
)
from travel_agent.api.routes.refine import (
    _EMPTY_POOL_TEXT,
    _apply_refine_filters,
    _merge_replan_intent,
    _refine_generator,
    _resolve_profile,
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
from travel_agent.coordinator.streaming import StreamEventType

# ── helpers ───────────────────────────────────────────────────────────────────

_WINDOW = Window(start_date=date(2026, 12, 10), end_date=date(2026, 12, 17))
_INTENT = TravelIntent(
    origin_iata="DEL",
    destination_iata="DXB",
    earliest_departure=date(2026, 12, 1),
    latest_departure=date(2026, 12, 31),
    budget_inr=30000,
)


def _flight(price: int, stops: int, dep_hour: int = 10, duration: int = 225) -> FlightOption:
    dep = f"2026-12-10T{dep_hour:02d}:00:00"
    arr = f"2026-12-10T{(dep_hour + duration // 60):02d}:{duration % 60:02d}:00"
    return FlightOption(
        window=_WINDOW,
        provider="synthetic",
        origin_iata="DEL",
        destination_iata="DXB",
        outbound_departure_at=dep,
        outbound_arrival_at=arr,
        airline_code="EK",
        flight_number=f"EK-{price}",
        cabin_class=CabinClass.ECONOMY,
        price_inr=price,
        outbound_duration_minutes=duration,
        layover_count=stops,
        is_refundable=False,
    )


_POOL = [
    _flight(10000, stops=1, dep_hour=8),
    _flight(20000, stops=0, dep_hour=14),
    _flight(30000, stops=2, dep_hour=19),
    _flight(40000, stops=0, dep_hour=22),
]


def _archetype(flight: FlightOption) -> Archetype:
    return Archetype(
        label=ArchetypeLabel.BEST_VALUE,
        flight=flight,
        explanation="Best value",
        deeplink_url="https://example.com",
    )


async def _collect(gen: AsyncGenerator[str, None]) -> list[dict]:
    import json

    events: list[dict] = []
    async for line in gen:
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ── _resolve_profile ──────────────────────────────────────────────────────────


def test_resolve_profile_known_returns_as_is() -> None:
    assert _resolve_profile("demo-llama") == "demo-llama"


def test_resolve_profile_haiku_returns_as_is() -> None:
    assert _resolve_profile("demo-haiku") == "demo-haiku"


def test_resolve_profile_unknown_demo_env_returns_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "demo")
    assert _resolve_profile("not-a-profile") == "demo-haiku"


def test_resolve_profile_unknown_non_demo_env_returns_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "prod")
    assert _resolve_profile(None) == "prod"


# ── _apply_refine_filters — price ─────────────────────────────────────────────


def test_filter_price_max_keeps_under_threshold() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(price_max_inr=25000, sort_by="price"))
    prices = [f.price_inr for f in result]
    assert all(p <= 25000 for p in prices)
    assert 10000 in prices
    assert 20000 in prices


def test_filter_price_min_removes_cheap() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(price_min_inr=25000, sort_by="price"))
    assert all(f.price_inr >= 25000 for f in result)


def test_filter_price_combined() -> None:
    result = _apply_refine_filters(
        _POOL, RefineArgs(price_min_inr=15000, price_max_inr=35000, sort_by="price")
    )
    assert all(15000 <= f.price_inr <= 35000 for f in result)


def test_filter_price_max_no_match_returns_empty() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(price_max_inr=5000, sort_by="price"))
    assert result == []


# ── _apply_refine_filters — stops ─────────────────────────────────────────────


def test_filter_direct_only_removes_layovers() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(direct_only=True, sort_by="price"))
    assert all(f.layover_count == 0 for f in result)
    assert len(result) == 2


def test_filter_max_layover_count() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(max_layover_count=1, sort_by="price"))
    assert all(f.layover_count <= 1 for f in result)


def test_filter_direct_only_takes_precedence_over_max_layover() -> None:
    result = _apply_refine_filters(
        _POOL, RefineArgs(direct_only=True, max_layover_count=5, sort_by="price")
    )
    assert all(f.layover_count == 0 for f in result)


# ── _apply_refine_filters — departure window ──────────────────────────────────


def test_filter_morning_keeps_0600_to_1159() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(departure_window="morning", sort_by="price"))
    assert len(result) == 1
    assert result[0].price_inr == 10000  # dep_hour=8


def test_filter_afternoon_keeps_1200_to_1659() -> None:
    result = _apply_refine_filters(
        _POOL, RefineArgs(departure_window="afternoon", sort_by="price")
    )
    assert len(result) == 1
    assert result[0].price_inr == 20000  # dep_hour=14


def test_filter_evening_keeps_1700_to_2059() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(departure_window="evening", sort_by="price"))
    assert len(result) == 1
    assert result[0].price_inr == 30000  # dep_hour=19


def test_filter_night_keeps_2100_and_after() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(departure_window="night", sort_by="price"))
    assert len(result) == 1
    assert result[0].price_inr == 40000  # dep_hour=22


# ── _apply_refine_filters — sort ──────────────────────────────────────────────


def test_sort_by_price_ascending() -> None:
    pool = [_flight(30000, 0), _flight(10000, 0), _flight(20000, 0)]
    result = _apply_refine_filters(pool, RefineArgs(sort_by="price"))
    assert [f.price_inr for f in result] == [10000, 20000, 30000]


def test_sort_by_duration() -> None:
    pool = [_flight(10000, 0, duration=300), _flight(20000, 0, duration=200)]
    result = _apply_refine_filters(pool, RefineArgs(sort_by="duration"))
    assert result[0].outbound_duration_minutes == 200


def test_sort_by_stops() -> None:
    pool = [_flight(10000, stops=2), _flight(20000, stops=0), _flight(30000, stops=1)]
    result = _apply_refine_filters(pool, RefineArgs(sort_by="stops"))
    assert [f.layover_count for f in result] == [0, 1, 2]


# ── _apply_refine_filters — clear_filters ─────────────────────────────────────


def test_clear_filters_returns_original_pool() -> None:
    result = _apply_refine_filters(_POOL, RefineArgs(clear_filters=True, sort_by="price"))
    assert result == list(_POOL)


# ── _merge_replan_intent ──────────────────────────────────────────────────────


def test_merge_all_none_returns_copy_of_base() -> None:
    result = _merge_replan_intent(_INTENT, ReplanArgs())
    assert result.origin_iata == _INTENT.origin_iata
    assert result.destination_iata == _INTENT.destination_iata
    assert result is not _INTENT


def test_merge_destination_override() -> None:
    result = _merge_replan_intent(_INTENT, ReplanArgs(destination_iata="SIN"))
    assert result.destination_iata == "SIN"
    assert result.origin_iata == _INTENT.origin_iata


def test_merge_departure_window() -> None:
    args = ReplanArgs(
        departure_window_start=date(2026, 11, 1),
        departure_window_end=date(2026, 11, 30),
    )
    result = _merge_replan_intent(_INTENT, args)
    assert result.earliest_departure == date(2026, 11, 1)
    assert result.latest_departure == date(2026, 11, 30)


def test_merge_budget_maps_to_budget_inr() -> None:
    result = _merge_replan_intent(_INTENT, ReplanArgs(budget_max_inr=50000))
    assert result.budget_inr == 50000


def test_merge_preferred_airlines_joined() -> None:
    result = _merge_replan_intent(_INTENT, ReplanArgs(preferred_airlines=["EK", "AI"]))
    assert result.airline_preference == "EK, AI"


# ── _refine_generator — cache miss ────────────────────────────────────────────


async def test_generator_cache_miss_emits_error() -> None:
    with (
        patch("travel_agent.api.routes.refine.search_cache") as mock_cache,
        patch("travel_agent.api.routes.refine.get_langfuse", return_value=None),
    ):
        mock_cache.get = AsyncMock(return_value=None)
        events = await _collect(_refine_generator("no-such-id", "cheaper", "demo-haiku"))

    types = [e["type"] for e in events]
    assert StreamEventType.CONVERSATION_THINKING in types
    assert StreamEventType.ERROR in types
    error = next(e for e in events if e["type"] == StreamEventType.ERROR)
    assert "expired" in error["message"].lower()


# ── _refine_generator — NO_OP path ───────────────────────────────────────────


async def test_generator_no_op_emits_conversation_message() -> None:
    explanation = "I help refine flight searches. Want to filter options?"
    output = ConversationManagerOutput(
        action=ConversationAction.NO_OP,
        no_op_args=NoOpArgs(explanation=explanation),
    )

    mock_agent = MagicMock()
    mock_agent.understand = AsyncMock(return_value=output)
    mock_client = MagicMock()

    with (
        patch("travel_agent.api.routes.refine.search_cache") as mock_cache,
        patch("travel_agent.api.routes.refine.get_langfuse", return_value=None),
        patch(
            "travel_agent.api.routes.refine.get_llm_client_and_model",
            return_value=(mock_client, "llama"),
        ),
        patch(
            "travel_agent.api.routes.refine.ConversationManagerAgent",
            return_value=mock_agent,
        ),
    ):
        mock_cache.get = AsyncMock(return_value=(_INTENT, _POOL))
        events = await _collect(_refine_generator("req-1", "what's the weather?", "demo-llama"))

    types = [e["type"] for e in events]
    assert StreamEventType.CONVERSATION_THINKING in types
    assert StreamEventType.CONVERSATION_ACTION_CLASSIFIED in types
    assert StreamEventType.CONVERSATION_MESSAGE in types
    msg = next(e for e in events if e["type"] == StreamEventType.CONVERSATION_MESSAGE)
    assert msg["text"] == explanation


# ── _refine_generator — REFINE path (happy path) ─────────────────────────────


async def test_generator_refine_emits_archetypes() -> None:
    output = ConversationManagerOutput(
        action=ConversationAction.REFINE,
        refine_args=RefineArgs(price_max_inr=25000, sort_by="price"),
        args_summary="Under ₹25,000",
    )

    arch = _archetype(_POOL[0])
    mock_state = RequestState(
        raw_input="",
        intent=_INTENT,
        flight_options=[_POOL[0]],
        archetypes=[arch],
    )
    mock_optimizer = MagicMock()
    mock_optimizer.run = AsyncMock(return_value=mock_state)
    mock_agent = MagicMock()
    mock_agent.understand = AsyncMock(return_value=output)
    mock_client = MagicMock()

    with (
        patch("travel_agent.api.routes.refine.search_cache") as mock_cache,
        patch("travel_agent.api.routes.refine.get_langfuse", return_value=None),
        patch(
            "travel_agent.api.routes.refine.get_llm_client_and_model",
            return_value=(mock_client, "haiku"),
        ),
        patch(
            "travel_agent.api.routes.refine.ConversationManagerAgent",
            return_value=mock_agent,
        ),
        patch(
            "travel_agent.api.routes.refine.OptimizerAgent",
            return_value=mock_optimizer,
        ),
    ):
        mock_cache.get = AsyncMock(return_value=(_INTENT, _POOL))
        mock_cache.put = AsyncMock()
        events = await _collect(_refine_generator("req-1", "under 25k", "demo-haiku"))

    types = [e["type"] for e in events]
    assert StreamEventType.CONVERSATION_THINKING in types
    assert StreamEventType.OPTIMIZER_STARTED in types
    assert StreamEventType.ARCHETYPE_READY in types
    assert StreamEventType.DONE in types
    done = next(e for e in events if e["type"] == StreamEventType.DONE)
    assert done["request_id"] == "req-1"


# ── _refine_generator — REFINE empty pool ────────────────────────────────────


async def test_generator_refine_empty_pool_emits_conversation_message() -> None:
    output = ConversationManagerOutput(
        action=ConversationAction.REFINE,
        refine_args=RefineArgs(price_max_inr=1, sort_by="price"),
        args_summary="Under ₹1",
    )
    mock_agent = MagicMock()
    mock_agent.understand = AsyncMock(return_value=output)
    mock_client = MagicMock()

    with (
        patch("travel_agent.api.routes.refine.search_cache") as mock_cache,
        patch("travel_agent.api.routes.refine.get_langfuse", return_value=None),
        patch(
            "travel_agent.api.routes.refine.get_llm_client_and_model",
            return_value=(mock_client, "haiku"),
        ),
        patch(
            "travel_agent.api.routes.refine.ConversationManagerAgent",
            return_value=mock_agent,
        ),
    ):
        mock_cache.get = AsyncMock(return_value=(_INTENT, _POOL))
        events = await _collect(_refine_generator("req-1", "under 1 rupee", "demo-haiku"))

    types = [e["type"] for e in events]
    assert StreamEventType.CONVERSATION_MESSAGE in types
    msg = next(e for e in events if e["type"] == StreamEventType.CONVERSATION_MESSAGE)
    assert msg["text"] == _EMPTY_POOL_TEXT


# ── _refine_generator — REPLAN path ──────────────────────────────────────────


async def test_generator_replan_delegates_to_stream_replan() -> None:
    output = ConversationManagerOutput(
        action=ConversationAction.REPLAN,
        replan_args=ReplanArgs(destination_iata="SIN"),
        args_summary="Searching Delhi to Singapore",
    )

    mock_agent = MagicMock()
    mock_agent.understand = AsyncMock(return_value=output)
    mock_client = MagicMock()

    async def _fake_replan(*_: Any, **__: Any) -> AsyncGenerator[dict, None]:
        yield {"type": "search_done", "total_options": 5}
        yield {"type": "done", "request_id": "new-req"}

    with (
        patch("travel_agent.api.routes.refine.search_cache") as mock_cache,
        patch("travel_agent.api.routes.refine.get_langfuse", return_value=None),
        patch(
            "travel_agent.api.routes.refine.get_llm_client_and_model",
            return_value=(mock_client, "haiku"),
        ),
        patch(
            "travel_agent.api.routes.refine.ConversationManagerAgent",
            return_value=mock_agent,
        ),
        patch(
            "travel_agent.api.routes.refine.OptimizerAgent",
            return_value=MagicMock(),
        ),
        patch("travel_agent.api.routes.refine.stream_replan", _fake_replan),
    ):
        mock_cache.get = AsyncMock(return_value=(_INTENT, _POOL))
        events = await _collect(_refine_generator("req-1", "try Singapore", "demo-haiku"))

    types = [e["type"] for e in events]
    assert StreamEventType.CONVERSATION_THINKING in types
    assert StreamEventType.CONVERSATION_ACTION_CLASSIFIED in types
    assert "search_done" in types
    assert "done" in types
