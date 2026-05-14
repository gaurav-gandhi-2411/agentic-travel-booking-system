"""Tests for the coordinator state models (RequestState, TravelIntent, etc.)."""
from datetime import date

from travel_agent.coordinator.state import (
    ArchetypeLabel,
    BookingPhase,
    BookingStatus,
    CabinClass,
    CallBudget,
    CoordinatorPhase,
    FlightOption,
    HotelOption,
    RequestState,
    TravelIntent,
    TripType,
    Window,
)


def _make_window() -> Window:
    return Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 8))


def _make_intent() -> TravelIntent:
    return TravelIntent(
        origin_iata="BOM",
        destination_iata="CDG",
        earliest_departure=date(2026, 6, 1),
        latest_departure=date(2026, 6, 30),
    )


# ── TravelIntent ──────────────────────────────────────────────────────────────


def test_intent_defaults() -> None:
    intent = _make_intent()
    assert intent.trip_duration_days == 7
    assert intent.traveler_count == 1
    assert intent.cabin_class == CabinClass.ECONOMY
    assert intent.trip_type == TripType.ROUND_TRIP
    assert intent.budget_inr is None


def test_intent_with_budget() -> None:
    intent = TravelIntent(
        origin_iata="DEL",
        destination_iata="NRT",
        earliest_departure=date(2026, 7, 1),
        latest_departure=date(2026, 7, 31),
        budget_inr=100_000,
        cabin_class=CabinClass.BUSINESS,
    )
    assert intent.budget_inr == 100_000
    assert intent.cabin_class == CabinClass.BUSINESS


# ── Window ────────────────────────────────────────────────────────────────────


def test_window_defaults() -> None:
    w = _make_window()
    assert w.interim_score == 0.0


def test_window_with_score() -> None:
    w = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 8), interim_score=0.85)
    assert w.interim_score == 0.85


# ── FlightOption ──────────────────────────────────────────────────────────────


def test_flight_option_id_auto_generated() -> None:
    w = _make_window()
    f = FlightOption(
        window=w,
        provider="synthetic",
        origin_iata="BOM",
        destination_iata="CDG",
        outbound_departure_at="2026-06-01T10:00:00Z",
        outbound_arrival_at="2026-06-01T22:00:00Z",
        airline_code="AI",
        flight_number="AI131",
        cabin_class=CabinClass.ECONOMY,
        price_inr=45_000,
        outbound_duration_minutes=480,
    )
    assert f.id  # non-empty UUID string
    assert f.layover_count == 0
    assert not f.is_refundable
    assert f.raw == {}


def test_two_flights_have_distinct_ids() -> None:
    w = _make_window()
    common = {
        "window": w,
        "provider": "synthetic",
        "origin_iata": "BOM",
        "destination_iata": "CDG",
        "outbound_departure_at": "2026-06-01T10:00:00Z",
        "outbound_arrival_at": "2026-06-01T22:00:00Z",
        "airline_code": "AI",
        "flight_number": "AI131",
        "cabin_class": CabinClass.ECONOMY,
        "price_inr": 45_000,
        "outbound_duration_minutes": 480,
    }
    f1 = FlightOption(**common)  # type: ignore[arg-type]
    f2 = FlightOption(**common)  # type: ignore[arg-type]
    assert f1.id != f2.id


# ── HotelOption ───────────────────────────────────────────────────────────────


def test_hotel_option_defaults() -> None:
    w = _make_window()
    h = HotelOption(
        window=w,
        provider="synthetic",
        name="Hotel Artemide",
        city="Paris",
        stars=4.0,
        review_score=8.5,
        price_per_night_inr=8_000,
        total_price_inr=56_000,
    )
    assert h.id
    assert h.location_description == ""
    assert not h.is_refundable


# ── CallBudget ────────────────────────────────────────────────────────────────


def test_call_budget_defaults() -> None:
    budget = CallBudget()
    assert budget.can_call_flight()
    assert budget.can_call_hotel()
    assert budget.can_call_llm()
    assert not budget.is_degraded()


def test_call_budget_flight_exhaustion() -> None:
    budget = CallBudget(flight_calls_used=150, flight_calls_max=150)
    assert not budget.can_call_flight()
    assert budget.is_degraded()


def test_call_budget_llm_exhaustion() -> None:
    budget = CallBudget(llm_calls_used=20, llm_calls_max=20)
    assert not budget.can_call_llm()
    assert budget.is_degraded()


# ── BookingStatus ─────────────────────────────────────────────────────────────


def test_booking_status_defaults() -> None:
    bs = BookingStatus()
    assert bs.phase == BookingPhase.IDLE
    assert bs.selected_package is None
    assert bs.pnr is None


# ── RequestState ──────────────────────────────────────────────────────────────


def test_request_state_defaults() -> None:
    state = RequestState()
    assert state.request_id  # auto-generated UUID
    assert state.phase == CoordinatorPhase.PLANNING
    assert state.intent is None
    assert state.flight_options == []
    assert state.hotel_options == []
    assert state.packages == []
    assert state.errors == []
    assert not state.is_partial


def test_request_state_two_instances_distinct_ids() -> None:
    s1 = RequestState()
    s2 = RequestState()
    assert s1.request_id != s2.request_id


def test_request_state_with_intent() -> None:
    intent = _make_intent()
    state = RequestState(raw_input="fly BOM to CDG", intent=intent)
    assert state.intent is not None
    assert state.intent.origin_iata == "BOM"


def test_request_state_serialization_roundtrip() -> None:
    state = RequestState(raw_input="test query", tenant_id="tenant-abc")
    dumped = state.model_dump()
    restored = RequestState.model_validate(dumped)
    assert restored.request_id == state.request_id
    assert restored.tenant_id == "tenant-abc"


def test_archetype_label_enum_values_distinct() -> None:
    assert ArchetypeLabel.BEST_VALUE != ArchetypeLabel.BEST_EXPERIENCE
