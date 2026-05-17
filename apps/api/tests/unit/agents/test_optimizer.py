"""Unit tests for OptimizerAgent."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import vcr

from travel_agent.agents.optimizer import OptimizerAgent, _flight_summary
from travel_agent.coordinator.state import (
    ArchetypeLabel,
    CabinClass,
    FlightOption,
    RequestState,
    TravelIntent,
    TripType,
    Window,
)
from travel_agent.llm.base import LLMClient, LLMResponse, ToolCall
from travel_agent.llm.anthropic import AnthropicAdapter

_WINDOW = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))

_CASSETTE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "cassettes"
_CLOCK_TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:[AP]M|am|pm)?|morning departure|evening departure")


def _flight(
    price_inr: int,
    layover_count: int = 0,
    outbound_duration_minutes: int = 540,
    airline: str = "6E",
    flight_number: str = "6E-100",
    outbound_departure_at: str = "2026-06-01T09:00:00+05:30",
    outbound_arrival_at: str = "2026-06-01T18:00:00+05:30",
    raw: dict[str, Any] | None = None,
) -> FlightOption:
    return FlightOption(
        window=_WINDOW,
        provider="synthetic",
        origin_iata="BOM",
        destination_iata="CDG",
        airline_code=airline,
        flight_number=flight_number,
        cabin_class=CabinClass.ECONOMY,
        price_inr=price_inr,
        outbound_departure_at=outbound_departure_at,
        outbound_arrival_at=outbound_arrival_at,
        outbound_duration_minutes=outbound_duration_minutes,
        layover_count=layover_count,
        raw=raw or {},
    )


def _mock_client(explanation: str = "Great choice for the budget traveler.") -> LLMClient:
    response = LLMResponse(
        content="",
        model="claude-sonnet-4-6",
        input_tokens=50,
        output_tokens=30,
        latency_ms=0.0,
        tool_calls=[
            ToolCall(
                name="generate_archetype_explanation",
                input={"explanation": explanation},
                id="tc-opt-001",
            )
        ],
    )
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=response)
    return client


def _make_state(flights: list[FlightOption]) -> RequestState:
    state = RequestState()
    state.intent = TravelIntent(
        origin_iata="BOM",
        destination_iata="CDG",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 8),
        cabin_class=CabinClass.ECONOMY,
        trip_type=TripType.ROUND_TRIP,
        raw_query="test",
    )
    state.flight_options = flights
    return state


# ── basic archetype creation ──────────────────────────────────────────────────


async def test_produces_two_archetypes() -> None:
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    agent = OptimizerAgent(client=_mock_client(), partner_marker="12345")
    state = _make_state([lcc, premium])
    result = await agent.run(state, today=date(2026, 5, 14))
    assert len(result.archetypes) == 2


async def test_archetype_labels_present() -> None:
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    agent = OptimizerAgent(client=_mock_client(), partner_marker="12345")
    state = _make_state([lcc, premium])
    result = await agent.run(state, today=date(2026, 5, 14))
    labels = {a.label for a in result.archetypes}
    assert ArchetypeLabel.BEST_VALUE in labels
    assert ArchetypeLabel.BEST_EXPERIENCE in labels


async def test_cheap_lcc_is_best_value() -> None:
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    agent = OptimizerAgent(client=_mock_client(), partner_marker="12345")
    state = _make_state([lcc, premium])
    result = await agent.run(state, today=date(2026, 5, 14))
    value_arch = next(a for a in result.archetypes if a.label == ArchetypeLabel.BEST_VALUE)
    assert value_arch.flight.price_inr == 47_500


async def test_direct_premium_is_best_experience() -> None:
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    agent = OptimizerAgent(client=_mock_client(), partner_marker="12345")
    state = _make_state([lcc, premium])
    result = await agent.run(state, today=date(2026, 5, 14))
    exp_arch = next(a for a in result.archetypes if a.label == ArchetypeLabel.BEST_EXPERIENCE)
    assert exp_arch.flight.layover_count == 0


async def test_empty_flight_options_returns_unchanged() -> None:
    agent = OptimizerAgent(client=_mock_client())
    state = RequestState()
    result = await agent.run(state)
    assert result.archetypes == []


# ── explanation ───────────────────────────────────────────────────────────────


async def test_explanation_from_llm_used() -> None:
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    explanation = "Best value at INR 47,500 with 2 stops."
    agent = OptimizerAgent(client=_mock_client(explanation), partner_marker="12345")
    state = _make_state([lcc, premium])
    result = await agent.run(state, today=date(2026, 5, 14))
    assert any(a.explanation == explanation for a in result.archetypes)


async def test_fallback_explanation_when_no_client() -> None:
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    agent = OptimizerAgent(client=None)
    state = _make_state([lcc, premium])
    result = await agent.run(state, today=date(2026, 5, 14))
    for a in result.archetypes:
        assert len(a.explanation) > 10


async def test_fallback_explanation_when_llm_returns_no_tool_call() -> None:
    no_tool_response = LLMResponse(
        content="Here is the explanation.",
        model="claude-sonnet-4-6",
        input_tokens=20,
        output_tokens=10,
        latency_ms=0.0,
        tool_calls=[],
    )
    client = AsyncMock(spec=LLMClient)
    client.chat = AsyncMock(return_value=no_tool_response)
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    agent = OptimizerAgent(client=client)
    state = _make_state([lcc])
    result = await agent.run(state, today=date(2026, 5, 14))
    assert all(len(a.explanation) > 0 for a in result.archetypes)


# ── deeplink ──────────────────────────────────────────────────────────────────


async def test_deeplink_url_contains_partner_marker() -> None:
    flight = _flight(
        price_inr=47_500,
        raw={"link": "/search/BOM0106CDG08062026"},
    )
    agent = OptimizerAgent(client=_mock_client(), partner_marker="99999")
    state = _make_state([flight])
    result = await agent.run(state, today=date(2026, 5, 14))
    for a in result.archetypes:
        assert "99999" in a.deeplink_url


async def test_deeplink_empty_when_no_partner_marker() -> None:
    flight = _flight(price_inr=47_500)
    agent = OptimizerAgent(client=_mock_client(), partner_marker="")
    state = _make_state([flight])
    result = await agent.run(state, today=date(2026, 5, 14))
    for a in result.archetypes:
        assert a.deeplink_url == ""


# ── score breakdown ───────────────────────────────────────────────────────────


async def test_score_breakdown_has_both_axes() -> None:
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    agent = OptimizerAgent(client=_mock_client(), partner_marker="12345")
    state = _make_state([lcc, premium])
    result = await agent.run(state, today=date(2026, 5, 14))
    for a in result.archetypes:
        assert "value_score" in a.score_breakdown
        assert "experience_score" in a.score_breakdown


# ── parallel explain calls ────────────────────────────────────────────────────


async def test_explain_called_twice_for_two_flights() -> None:
    """Both archetype explains should be called (parallelised via asyncio.gather)."""
    lcc = _flight(price_inr=47_500, layover_count=2, outbound_duration_minutes=1020)
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    mock_client = _mock_client()
    agent = OptimizerAgent(client=mock_client, partner_marker="12345")
    state = _make_state([lcc, premium])
    await agent.run(state, today=date(2026, 5, 14))
    # _explain is called twice (once per archetype) + _generate_comparisons once
    # Total chat calls: 2 explain + 1 comparisons = 3
    assert mock_client.chat.call_count >= 2


# ── bimodal synthetic data verification ──────────────────────────────────────


async def test_bimodal_synthetic_correct_archetype_assignment() -> None:
    # Replicate bimodal shape: LCC cluster (cheap, slow) vs premium (expensive, direct)
    flights = [
        _flight(
            price_inr=47_500,
            layover_count=2,
            outbound_duration_minutes=1020,
            outbound_departure_at="2026-06-01T02:30:00+05:30",
            outbound_arrival_at="2026-06-01T21:00:00+05:30",
        ),
        _flight(price_inr=55_000, layover_count=2, outbound_duration_minutes=1080),
        _flight(price_inr=62_000, layover_count=1, outbound_duration_minutes=900),
        _flight(
            price_inr=91_500,
            layover_count=0,
            outbound_duration_minutes=540,
            outbound_arrival_at="2026-06-01T14:00:00+05:30",
        ),
        _flight(
            price_inr=105_000,
            layover_count=0,
            outbound_duration_minutes=510,
            outbound_arrival_at="2026-06-01T13:00:00+05:30",
        ),
        _flight(price_inr=119_800, layover_count=0, outbound_duration_minutes=480),
    ]
    agent = OptimizerAgent(client=_mock_client(), partner_marker="12345")
    state = _make_state(flights)
    result = await agent.run(state, today=date(2026, 5, 14))

    value_arch = next(a for a in result.archetypes if a.label == ArchetypeLabel.BEST_VALUE)
    exp_arch = next(a for a in result.archetypes if a.label == ArchetypeLabel.BEST_EXPERIENCE)

    # LCC (cheap) should be best-value
    assert value_arch.flight.price_inr < 65_000
    # Premium (direct, fast) should be best-experience
    assert exp_arch.flight.layover_count == 0


# ── Issue #14: departure-time hallucination fix ───────────────────────────────


def test_system_prompt_no_departure_time_guidance() -> None:
    """Optimizer system prompt must prohibit citing departure/arrival times, not encourage it.

    Regression guard for Issue #14: Haiku was hallucinating departure times ('10:30 AM',
    '9:30 AM') by following the old 'mention arrival time' guidance in the system prompt.
    The fix removes encouraging language and adds an explicit prohibition.
    """
    prompt_path = (
        Path(__file__).parent.parent.parent.parent
        / "src" / "travel_agent" / "agents" / "prompts" / "optimizer_system.txt"
    )
    text = prompt_path.read_text()
    text_lower = text.lower()
    # Old encouraging phrases must be gone (the old prompt said "mention ... arrival time")
    assert "or arrival time" not in text_lower, (
        "Old 'mention ... or arrival time' instruction must be removed"
    )
    assert "daytime arrival" not in text_lower, "Old best-experience 'daytime arrival' guidance must be removed"
    # Prohibition must be present
    assert "do not" in text_lower and "departure times" in text_lower, (
        "Explicit 'Do NOT ... departure times' prohibition must be present"
    )


def test_flight_summary_excludes_departure_at() -> None:
    """_flight_summary must not include the departure datetime in its output.

    When outbound_departure_at is present in the FlightOption, the old implementation
    passed it verbatim ('Departs: 2026-06-01T09:00:00+05:30'), giving Haiku the
    raw time to cite in explanations. The fix removes this field from the summary.
    """
    flight = _flight(
        price_inr=91_500,
        layover_count=0,
        outbound_duration_minutes=540,
        outbound_departure_at="2026-06-01T09:30:00+05:30",
    )
    summary = _flight_summary(flight, ArchetypeLabel.BEST_EXPERIENCE)
    assert "09:30" not in summary, "Departure time must not appear in flight summary"
    assert "9:30" not in summary, "Departure time must not appear in flight summary"
    assert "Departs" not in summary, "'Departs:' field must be removed from summary"


async def test_explain_output_no_clock_time_vcr(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM explanation must not contain clock-time strings (VCR cassette, deterministic).

    Replays a recorded Anthropic response where the model correctly omits departure/arrival
    times from the explanation. Verifies that the cassette response clears the clock-time
    regex — if someone re-records the cassette with a time-citing response, this test fails.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    _vcr = vcr.VCR(
        cassette_library_dir=str(_CASSETTE_DIR / "optimizer"),
        record_mode="none",
        filter_headers=["authorization", "x-api-key"],
        decode_compressed_response=True,
        match_on=["method", "scheme", "host", "port", "path"],
    )
    with _vcr.use_cassette("explain_no_clock_time.yaml"):
        client = AnthropicAdapter()
        flight = _flight(
            price_inr=91_500,
            layover_count=0,
            outbound_duration_minutes=540,
            outbound_departure_at="2026-06-01T09:30:00+05:30",
        )
        agent = OptimizerAgent(client=client, model="claude-haiku-4-5-20251001")
        state = _make_state([flight])
        result = await agent.run(state, today=date(2026, 5, 17))

    assert result.archetypes, "Agent must produce archetypes"
    for arch in result.archetypes:
        assert not _CLOCK_TIME_RE.search(arch.explanation), (
            f"Explanation contains a clock time: {arch.explanation!r}"
        )
