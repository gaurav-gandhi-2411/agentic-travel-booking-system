"""Tests for the diversity matrix seed generation."""
from __future__ import annotations

from scripts.dataset.diversity_matrix import (
    AMBIGUITY_LEVELS,
    BUDGET_TIERS,
    DESTINATION_REGIONS,
    TRAVELER_PROFILES,
    TRIP_TYPES,
    Seed,
    iter_seeds,
)


def test_iter_seeds_returns_list_of_seeds() -> None:
    seeds = iter_seeds()
    assert seeds
    assert all(isinstance(s, Seed) for s in seeds)


def test_iter_seeds_full_combination_count() -> None:
    expected = (
        len(DESTINATION_REGIONS)
        * len(TRAVELER_PROFILES)
        * len(BUDGET_TIERS)
        * len(AMBIGUITY_LEVELS)
        * len(TRIP_TYPES)
    )
    assert len(iter_seeds()) == expected


def test_seeds_are_unique() -> None:
    seeds = iter_seeds()
    assert len(seeds) == len(set(seeds))


def test_all_axes_covered() -> None:
    seeds = iter_seeds()
    assert {s.destination_region for s in seeds} == set(DESTINATION_REGIONS)
    assert {s.traveler_profile for s in seeds} == set(TRAVELER_PROFILES)
    assert {s.budget_tier for s in seeds} == set(BUDGET_TIERS)
    assert {s.ambiguity_level for s in seeds} == set(AMBIGUITY_LEVELS)
    assert {s.trip_type for s in seeds} == set(TRIP_TYPES)
