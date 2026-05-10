"""Diversity matrix for synthetic dataset generation.

Defines the combination space that seeds example generation so the resulting
dataset covers a wide range of realistic travel scenarios. Generator samples
from this matrix to ensure coverage across all axes before random oversampling.

Full population of seeds: Phase 3.5.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Seed:
    destination_region: str
    traveler_profile: str
    budget_tier: str
    ambiguity_level: str
    trip_type: str


# Diversity axes — each value represents a distinct scenario dimension.
DESTINATION_REGIONS: list[str] = [
    "domestic_short_haul",   # e.g. NYC→BOS
    "domestic_long_haul",    # e.g. NYC→LAX
    "transatlantic",         # e.g. NYC→LHR
    "transpacific",          # e.g. LAX→NRT
    "intra_europe",          # e.g. CDG→BCN
    "emerging_market",       # e.g. DEL→BKK
]

TRAVELER_PROFILES: list[str] = [
    "solo_business",
    "solo_leisure",
    "couple_leisure",
    "family_with_children",
    "group_corporate",
    "senior_leisure",
]

BUDGET_TIERS: list[str] = [
    "ultra_budget",    # hostels, basic economy, strict per-diem
    "mid_range",       # 3-star hotels, economy/premium economy
    "premium",         # 4-star hotels, business class
    "luxury",          # 5-star, first class, no hard limit
]

AMBIGUITY_LEVELS: list[str] = [
    "fully_specified",    # all dates, destinations, preferences explicit
    "partial_dates",      # destination known, dates flexible
    "partial_dest",       # dates known, destination open (e.g. "somewhere warm")
    "high_ambiguity",     # minimal constraints, agent must clarify or infer
]

TRIP_TYPES: list[str] = [
    "one_way",
    "round_trip",
    "multi_city",
    "open_jaw",
]


def iter_seeds() -> list[Seed]:
    """Return all axis combinations as Seed objects.

    Full combination count:
    6 regions × 6 profiles × 4 tiers × 4 ambiguity × 4 trip_types = 2,304 seeds.
    Generator samples from this list; all 2,304 need not be generated.
    """
    seeds: list[Seed] = []
    for region in DESTINATION_REGIONS:
        for profile in TRAVELER_PROFILES:
            for tier in BUDGET_TIERS:
                for ambiguity in AMBIGUITY_LEVELS:
                    for trip_type in TRIP_TYPES:
                        seeds.append(
                            Seed(
                                destination_region=region,
                                traveler_profile=profile,
                                budget_tier=tier,
                                ambiguity_level=ambiguity,
                                trip_type=trip_type,
                            )
                        )
    return seeds
