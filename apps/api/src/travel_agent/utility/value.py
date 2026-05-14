"""Value score for a flight option.

Higher → more financially attractive for the traveler.
The score is in [0.0, 1.0] and is RELATIVE — only meaningful when
compared across options in the same search, not across searches.

Factors (all contribute additively to a penalty, then inverted):
  - price_inr: primary driver — cheaper is better
  - layover_count: each stop wastes time without adding value
  - red-eye penalty: departures 00:00-05:59 (local, approximated from ISO hour)
"""
from __future__ import annotations

import math

from travel_agent.coordinator.state import FlightOption

# Price reference for normalisation: roughly the 99th-pctile expected price
# for BOM international routes in INR. Scores saturate gracefully above this.
_PRICE_REF_INR = 200_000
_LAYOVER_PENALTY = 0.10  # per stop
_RED_EYE_PENALTY = 0.05  # for 00-05 departure hour
_RED_EYE_HOUR_END = 5


def value_score(flight: FlightOption) -> float:
    """Return a value score in [0.0, 1.0]; higher is more value-for-money."""
    # Price component (sigmoid-like, bounded)
    price_ratio = flight.price_inr / _PRICE_REF_INR
    price_component = 1.0 - (1.0 / (1.0 + math.exp(-8 * (price_ratio - 0.4))))

    # Layover penalty
    layover_deduction = min(flight.layover_count * _LAYOVER_PENALTY, 0.25)

    # Red-eye penalty — parse departure hour from ISO string
    depart_hour = _parse_hour(flight.outbound_departure_at)
    red_eye_deduction = _RED_EYE_PENALTY if 0 <= depart_hour <= _RED_EYE_HOUR_END else 0.0

    raw = price_component - layover_deduction - red_eye_deduction
    return max(0.0, min(1.0, raw))


def _parse_hour(iso_dt: str) -> int:
    """Extract the hour (0-23) from an ISO 8601 datetime string."""
    try:
        # "2026-06-01T02:30:00+05:30" → split on T → "02:30:00+05:30" → split on : → "02"
        time_part = iso_dt.split("T")[1]
        return int(time_part[:2])
    except (IndexError, ValueError):
        return 12  # safe default (midday, no penalty)
