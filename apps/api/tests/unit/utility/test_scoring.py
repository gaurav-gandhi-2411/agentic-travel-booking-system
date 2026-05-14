"""Unit tests for value_score and experience_score."""
from __future__ import annotations

from datetime import date

from travel_agent.coordinator.state import CabinClass, FlightOption, Window
from travel_agent.utility.experience import experience_score
from travel_agent.utility.value import value_score

_WINDOW = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))


def _flight(
    price_inr: int = 50_000,
    layover_count: int = 0,
    outbound_duration_minutes: int = 540,
    outbound_departure_at: str = "2026-06-01T09:00:00+05:30",
    outbound_arrival_at: str = "2026-06-01T18:00:00+05:30",
    cabin_class: CabinClass = CabinClass.ECONOMY,
) -> FlightOption:
    return FlightOption(
        window=_WINDOW,
        provider="synthetic",
        origin_iata="BOM",
        destination_iata="CDG",
        airline_code="6E",
        flight_number="6E-100",
        cabin_class=cabin_class,
        price_inr=price_inr,
        outbound_departure_at=outbound_departure_at,
        outbound_arrival_at=outbound_arrival_at,
        outbound_duration_minutes=outbound_duration_minutes,
        layover_count=layover_count,
    )


# ── value_score ────────────────────────────────────────────────────────────────


def test_value_score_range() -> None:
    f = _flight(price_inr=50_000)
    s = value_score(f)
    assert 0.0 <= s <= 1.0


def test_cheaper_flight_higher_value() -> None:
    cheap = _flight(price_inr=45_000)
    expensive = _flight(price_inr=120_000)
    assert value_score(cheap) > value_score(expensive)


def test_layovers_do_not_affect_value() -> None:
    # value_score is purely price-based; layover penalties belong in experience_score
    direct = _flight(layover_count=0)
    one_stop = _flight(layover_count=1)
    two_stop = _flight(layover_count=2)
    assert value_score(direct) == value_score(one_stop) == value_score(two_stop)


def test_departure_time_does_not_affect_value() -> None:
    # red-eye penalty removed from value_score; only experience_score discriminates
    red_eye = _flight(outbound_departure_at="2026-06-01T02:00:00+05:30")
    daytime = _flight(outbound_departure_at="2026-06-01T09:00:00+05:30")
    assert value_score(daytime) == value_score(red_eye)


def test_very_cheap_flight_near_top_of_range() -> None:
    cheap = _flight(price_inr=10_000)
    s = value_score(cheap)
    assert s > 0.7


def test_very_expensive_flight_low_value() -> None:
    expensive = _flight(price_inr=180_000)
    s = value_score(expensive)
    assert s < 0.3


# ── experience_score ──────────────────────────────────────────────────────────


def test_experience_score_range() -> None:
    f = _flight()
    s = experience_score(f)
    assert 0.0 <= s <= 1.0


def test_direct_flight_higher_experience() -> None:
    direct = _flight(layover_count=0)
    multi = _flight(layover_count=2)
    assert experience_score(direct) > experience_score(multi)


def test_shorter_duration_higher_experience() -> None:
    short = _flight(outbound_duration_minutes=300)
    long = _flight(outbound_duration_minutes=720)
    assert experience_score(short) > experience_score(long)


def test_business_class_higher_experience() -> None:
    economy = _flight(cabin_class=CabinClass.ECONOMY)
    business = _flight(cabin_class=CabinClass.BUSINESS)
    assert experience_score(business) > experience_score(economy)


def test_daytime_arrival_bonus() -> None:
    daytime = _flight(outbound_arrival_at="2026-06-01T14:00:00+05:30")
    late_night = _flight(outbound_arrival_at="2026-06-01T02:00:00+05:30")
    assert experience_score(daytime) > experience_score(late_night)


def test_premium_cluster_outscores_lcc_on_experience() -> None:
    # BOM-CDG synthetic premium: direct, 540 min, business
    premium = _flight(
        price_inr=91_500,
        layover_count=0,
        outbound_duration_minutes=540,
        cabin_class=CabinClass.ECONOMY,
        outbound_arrival_at="2026-06-01T14:00:00+05:30",
    )
    # BOM-CDG synthetic LCC: 2 stops, 1020 min
    lcc = _flight(
        price_inr=47_500,
        layover_count=2,
        outbound_duration_minutes=1020,
        cabin_class=CabinClass.ECONOMY,
        outbound_departure_at="2026-06-01T02:30:00+05:30",
        outbound_arrival_at="2026-06-01T21:00:00+05:30",
    )
    assert experience_score(premium) > experience_score(lcc)


def test_morning_departure_higher_experience() -> None:
    morning = _flight(outbound_departure_at="2026-06-01T09:00:00+05:30")
    red_eye = _flight(outbound_departure_at="2026-06-01T03:00:00+05:30")
    assert experience_score(morning) > experience_score(red_eye)


def test_lcc_cluster_outscores_premium_on_value() -> None:
    premium = _flight(price_inr=91_500, layover_count=0, outbound_duration_minutes=540)
    lcc = _flight(
        price_inr=47_500,
        layover_count=2,
        outbound_duration_minutes=1020,
        outbound_departure_at="2026-06-01T09:00:00+05:30",
    )
    assert value_score(lcc) > value_score(premium)
