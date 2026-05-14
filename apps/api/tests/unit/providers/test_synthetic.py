"""ADR-0013 statistical property tests for SyntheticProvider.

These tests verify data integrity guarantees that the provider contract
promises:
  - correct per-route counts
  - bimodal price distributions with a clear gap zone on every route
  - hotel star distribution skewed toward budget/mid tiers
  - the three "weird" hotels exist with their expected anomalies
  - deterministic output for identical inputs
"""

from __future__ import annotations

from datetime import date

import pytest

from travel_agent.coordinator.state import Window
from travel_agent.providers.synthetic import SyntheticProvider

# ── fixtures ──────────────────────────────────────────────────────────────────

_ROUTES = [
    ("BOM", "CDG"),
    ("BOM", "NRT"),
    ("BOM", "DPS"),
]

_CITIES = ["Paris", "Tokyo", "Bali"]

_CITY_COUNTS = {"Paris": 8, "Tokyo": 7, "Bali": 5}

_BIMODAL_GAP_THRESHOLD = 15_000  # gap must be at least ₹15 000 wide


@pytest.fixture
def window() -> Window:
    return Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))


@pytest.fixture
def provider() -> SyntheticProvider:
    return SyntheticProvider()


# ── flight count ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("origin", "destination"), _ROUTES)
def test_flight_count_per_route(
    provider: SyntheticProvider, window: Window, origin: str, destination: str
) -> None:
    flights = provider.get_flights(origin, destination, window)
    assert len(flights) == 10, f"{origin}-{destination}: expected 10 flights, got {len(flights)}"


def test_unknown_route_returns_empty(provider: SyntheticProvider, window: Window) -> None:
    assert provider.get_flights("BOM", "LHR", window) == []


# ── bimodal gap zone (ADR-0013 §3) ───────────────────────────────────────────


@pytest.mark.parametrize(("origin", "destination"), _ROUTES)
def test_bimodal_gap_exists(
    provider: SyntheticProvider, window: Window, origin: str, destination: str
) -> None:
    prices = sorted(f.price_inr for f in provider.get_flights(origin, destination, window))

    jumps = [
        (prices[i], prices[i + 1])
        for i in range(len(prices) - 1)
        if prices[i + 1] - prices[i] >= _BIMODAL_GAP_THRESHOLD
    ]
    assert len(jumps) == 1, (
        f"{origin}-{destination}: expected exactly one price gap ≥ ₹{_BIMODAL_GAP_THRESHOLD:,}, "
        f"got {len(jumps)}: {jumps}"
    )


@pytest.mark.parametrize(("origin", "destination"), _ROUTES)
def test_both_clusters_non_empty(
    provider: SyntheticProvider, window: Window, origin: str, destination: str
) -> None:
    prices = sorted(f.price_inr for f in provider.get_flights(origin, destination, window))
    gaps = [
        i for i in range(len(prices) - 1) if prices[i + 1] - prices[i] >= _BIMODAL_GAP_THRESHOLD
    ]
    gap_idx = gaps[0]
    lcc_count = gap_idx + 1
    premium_count = len(prices) - gap_idx - 1
    assert lcc_count >= 4, f"{origin}-{destination}: LCC cluster has only {lcc_count} flights"
    assert premium_count >= 4, (
        f"{origin}-{destination}: premium cluster has only {premium_count} flights"
    )


# ── hotel counts ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("city", _CITIES)
def test_hotel_count_per_city(provider: SyntheticProvider, window: Window, city: str) -> None:
    hotels = provider.get_hotels(city, window, nights=7)
    assert len(hotels) == _CITY_COUNTS[city], (
        f"{city}: expected {_CITY_COUNTS[city]} hotels, got {len(hotels)}"
    )


def test_unknown_city_returns_empty(provider: SyntheticProvider, window: Window) -> None:
    assert provider.get_hotels("Atlantis", window, nights=7) == []


# ── hotel star distribution ───────────────────────────────────────────────────


def test_star_distribution_skewed_to_budget(provider: SyntheticProvider, window: Window) -> None:
    all_hotels = []
    for city in _CITIES:
        all_hotels.extend(provider.get_hotels(city, window, nights=7))

    three_star = sum(1 for h in all_hotels if h.stars == 3.0)
    five_star = sum(1 for h in all_hotels if h.stars == 5.0)
    assert three_star > five_star, (
        f"Expected more 3★ hotels than 5★ (got {three_star} vs {five_star})"
    )
    assert three_star >= len(all_hotels) // 3, (
        f"Expected at least 1/3 of hotels to be 3★ (got {three_star}/{len(all_hotels)})"
    )


def test_min_stars_filter(provider: SyntheticProvider, window: Window) -> None:
    all_paris = provider.get_hotels("Paris", window, nights=7)
    four_plus = provider.get_hotels("Paris", window, nights=7, min_stars=4.0)
    assert len(four_plus) < len(all_paris)
    assert all(h.stars >= 4.0 for h in four_plus)


# ── weird hotels ──────────────────────────────────────────────────────────────


def test_budget_palace_montmartre_is_overpriced_three_star(
    provider: SyntheticProvider, window: Window
) -> None:
    paris = provider.get_hotels("Paris", window, nights=7)
    weird = next((h for h in paris if h.name == "Budget Palace Montmartre"), None)
    assert weird is not None, "Budget Palace Montmartre not found in Paris hotels"
    assert weird.stars == 3.0

    other_three_star = [h for h in paris if h.stars == 3.0 and h.name != weird.name]
    avg_3star_price = sum(h.price_per_night_inr for h in other_three_star) / len(other_three_star)
    assert weird.price_per_night_inr > avg_3star_price, (
        f"Budget Palace Montmartre should cost more than average 3★ "
        f"(₹{weird.price_per_night_inr:,} vs avg ₹{avg_3star_price:,.0f})"
    )


def test_le_petit_bijou_is_underpriced_four_star(
    provider: SyntheticProvider, window: Window
) -> None:
    paris = provider.get_hotels("Paris", window, nights=7)
    weird = next((h for h in paris if h.name == "Le Petit Bijou"), None)
    assert weird is not None, "Le Petit Bijou not found in Paris hotels"
    assert weird.stars == 4.0

    three_star_prices = [h.price_per_night_inr for h in paris if h.stars == 3.0]
    median_3star = sorted(three_star_prices)[len(three_star_prices) // 2]
    assert weird.price_per_night_inr < median_3star, (
        f"Le Petit Bijou (4★) should cost less than median 3★ price "
        f"(₹{weird.price_per_night_inr:,} vs median ₹{median_3star:,})"
    )
    assert weird.review_score >= 9.0, "Le Petit Bijou should have an excellent review score"


def test_tokyo_central_hostel_has_mediocre_reviews_great_location(
    provider: SyntheticProvider, window: Window
) -> None:
    tokyo = provider.get_hotels("Tokyo", window, nights=7)
    weird = next((h for h in tokyo if h.name == "Tokyo Central Hostel Business"), None)
    assert weird is not None, "Tokyo Central Hostel Business not found in Tokyo hotels"
    assert weird.review_score < 7.0, "Tokyo Central Hostel should have mediocre reviews"
    assert "Shinjuku" in weird.location_description, (
        "Tokyo Central Hostel should be in Shinjuku (great location)"
    )


# ── total_price_inr computation ───────────────────────────────────────────────


def test_hotel_total_price_is_nights_times_nightly(
    provider: SyntheticProvider, window: Window
) -> None:
    hotels = provider.get_hotels("Paris", window, nights=5)
    for h in hotels:
        assert h.total_price_inr == h.price_per_night_inr * 5


# ── determinism ───────────────────────────────────────────────────────────────


def test_get_flights_is_deterministic(provider: SyntheticProvider, window: Window) -> None:
    first = provider.get_flights("BOM", "CDG", window)
    second = provider.get_flights("BOM", "CDG", window)
    assert [f.id for f in first] == [f.id for f in second]
    assert [f.price_inr for f in first] == [f.price_inr for f in second]


def test_get_hotels_is_deterministic(provider: SyntheticProvider, window: Window) -> None:
    first = provider.get_hotels("Tokyo", window, nights=7)
    second = provider.get_hotels("Tokyo", window, nights=7)
    assert [h.id for h in first] == [h.id for h in second]
    assert [h.price_per_night_inr for h in first] == [h.price_per_night_inr for h in second]


def test_different_windows_produce_different_ids(
    provider: SyntheticProvider,
) -> None:
    w1 = Window(start_date=date(2026, 6, 1), end_date=date(2026, 6, 7))
    w2 = Window(start_date=date(2026, 6, 2), end_date=date(2026, 6, 8))
    flights_w1 = provider.get_flights("BOM", "CDG", w1)
    flights_w2 = provider.get_flights("BOM", "CDG", w2)
    ids_w1 = {f.id for f in flights_w1}
    ids_w2 = {f.id for f in flights_w2}
    assert ids_w1.isdisjoint(ids_w2), "Different windows must produce distinct flight IDs"
