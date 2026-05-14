"""Unit tests for HotelHunterAgent."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from travel_agent.agents.hotel_hunter import HotelHunterAgent
from travel_agent.coordinator.state import (
    CabinClass,
    HotelOption,
    RequestState,
    TravelIntent,
    TripType,
    Window,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

_WINDOW = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))


def _make_intent(
    destination: str = "CDG",
    hotel_min_stars: float = 3.0,
    trip_duration_days: int = 7,
) -> TravelIntent:
    return TravelIntent(
        origin_iata="BOM",
        destination_iata=destination,
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 8),
        cabin_class=CabinClass.ECONOMY,
        trip_type=TripType.ROUND_TRIP,
        hotel_min_stars=hotel_min_stars,
        trip_duration_days=trip_duration_days,
        raw_query="fly from Mumbai to Paris next month",
    )


def _make_state(
    intent: TravelIntent | None = None,
    windows: list[Window] | None = None,
    budget_overrides: dict[str, int] | None = None,
) -> RequestState:
    state = RequestState()
    state.intent = intent or _make_intent()
    state.candidate_windows = windows if windows is not None else [_WINDOW]
    if budget_overrides:
        for k, v in budget_overrides.items():
            setattr(state.call_budget, k, v)
    return state


def _stub_hotel(name: str = "Le Grand Hotel", stars: float = 4.0) -> HotelOption:
    return HotelOption(
        window=_WINDOW,
        provider="synthetic",
        name=name,
        city="Paris",
        stars=stars,
        review_score=8.5,
        price_per_night_inr=8000,
        total_price_inr=56000,
    )


# ── basic behaviour ───────────────────────────────────────────────────────────


async def test_no_intent_returns_unchanged() -> None:
    agent = HotelHunterAgent()
    state = RequestState()
    result = await agent.run(state)
    assert result.hotel_options == []
    assert result.call_budget.hotel_calls_used == 0


async def test_no_windows_returns_unchanged() -> None:
    agent = HotelHunterAgent()
    state = RequestState()
    state.intent = _make_intent()
    state.candidate_windows = []
    result = await agent.run(state)
    assert result.hotel_options == []


async def test_unknown_destination_returns_unchanged() -> None:
    agent = HotelHunterAgent()
    state = _make_state(intent=_make_intent(destination="XXX"))
    result = await agent.run(state)
    assert result.hotel_options == []
    assert result.call_budget.hotel_calls_used == 0


async def test_known_destination_cdg_returns_hotels() -> None:
    agent = HotelHunterAgent()
    state = _make_state(intent=_make_intent(destination="CDG"))
    result = await agent.run(state)
    assert len(result.hotel_options) > 0


async def test_known_destination_nrt_returns_hotels() -> None:
    agent = HotelHunterAgent()
    state = _make_state(intent=_make_intent(destination="NRT"))
    result = await agent.run(state)
    assert len(result.hotel_options) > 0


async def test_known_destination_dps_returns_hotels() -> None:
    agent = HotelHunterAgent()
    state = _make_state(intent=_make_intent(destination="DPS"))
    result = await agent.run(state)
    assert len(result.hotel_options) > 0


# ── star filtering ─────────────────────────────────────────────────────────────


async def test_star_filter_applied() -> None:
    agent = HotelHunterAgent()
    state = _make_state(intent=_make_intent(destination="CDG", hotel_min_stars=4.0))
    result = await agent.run(state)
    assert all(h.stars >= 4.0 for h in result.hotel_options)


async def test_low_star_filter_includes_more_hotels() -> None:
    agent = HotelHunterAgent()
    state_2 = _make_state(intent=_make_intent(destination="CDG", hotel_min_stars=2.0))
    state_4 = _make_state(intent=_make_intent(destination="CDG", hotel_min_stars=4.0))
    result_2 = await agent.run(state_2)
    result_4 = await agent.run(state_4)
    assert len(result_2.hotel_options) >= len(result_4.hotel_options)


# ── call budget ───────────────────────────────────────────────────────────────


async def test_tracks_call_count_per_window() -> None:
    agent = HotelHunterAgent()
    windows = [
        Window(start_date=date(2026, 6, d), end_date=date(2026, 6, d + 6)) for d in range(1, 4)
    ]
    state = _make_state(windows=windows)
    result = await agent.run(state)
    assert result.call_budget.hotel_calls_used == 3


async def test_respects_hotel_call_budget() -> None:
    agent = HotelHunterAgent()
    windows = [
        Window(start_date=date(2026, 6, d), end_date=date(2026, 6, d + 6)) for d in range(1, 4)
    ]
    state = _make_state(windows=windows, budget_overrides={"hotel_calls_used": 99})
    result = await agent.run(state)
    assert result.call_budget.hotel_calls_used == 100
    assert result.is_partial is True


# ── provider mock ─────────────────────────────────────────────────────────────


async def test_result_hotels_have_correct_provider() -> None:
    agent = HotelHunterAgent()
    state = _make_state()
    result = await agent.run(state)
    assert all(h.provider == "synthetic" for h in result.hotel_options)


async def test_provider_called_with_city_not_iata() -> None:
    agent = HotelHunterAgent()
    with patch.object(agent._provider, "get_hotels", wraps=agent._provider.get_hotels) as spy:
        state = _make_state(intent=_make_intent(destination="CDG"))
        await agent.run(state)
        call_args = spy.call_args
        assert call_args[0][0] == "Paris"


async def test_trip_duration_passed_as_nights() -> None:
    agent = HotelHunterAgent()
    with patch.object(agent._provider, "get_hotels", wraps=agent._provider.get_hotels) as spy:
        state = _make_state(intent=_make_intent(destination="CDG", trip_duration_days=10))
        await agent.run(state)
        call_args = spy.call_args
        assert call_args[0][2] == 10  # nights argument


async def test_result_is_same_state_object() -> None:
    agent = HotelHunterAgent()
    state = _make_state()
    result = await agent.run(state)
    assert result is state


async def test_multiple_windows_accumulate_hotels() -> None:
    agent = HotelHunterAgent()
    windows = [
        Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7)),
        Window(start_date=date(2026, 6, 2), end_date=date(2026, 6, 8)),
    ]
    state = _make_state(windows=windows)
    result = await agent.run(state)
    # SyntheticProvider returns same hotels per window, so 2x the count
    count_1 = len(HotelHunterAgent()._provider.get_hotels("Paris", windows[0], 7, 3.0))
    assert len(result.hotel_options) == count_1 * 2
