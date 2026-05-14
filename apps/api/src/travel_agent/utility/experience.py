"""Experience score for a flight option.

Higher → better travel experience (comfort, time efficiency, convenience).

Factors:
  - Total outbound duration: shorter is better
  - Direct flight bonus: layover_count == 0 is strongly preferred
  - Arrival time: daytime arrivals (08-20) are preferred over late-night
  - Cabin class bonus: business/first get a boost (premium experience)
"""
from __future__ import annotations

from travel_agent.coordinator.state import CabinClass, FlightOption

# Duration reference: 15 hours (900 min) is a very long haul — scores saturate below this
_DURATION_REF_MIN = 900
_DIRECT_BONUS = 0.20
_CABIN_BONUS = {
    CabinClass.ECONOMY: 0.0,
    CabinClass.PREMIUM_ECONOMY: 0.05,
    CabinClass.BUSINESS: 0.12,
    CabinClass.FIRST: 0.15,
}
_DAYTIME_BONUS = 0.04  # arrival between 08:00 and 20:59
_DAYTIME_START = 8
_DAYTIME_END = 20


def experience_score(flight: FlightOption) -> float:
    """Return an experience score in [0.0, 1.0]; higher is more comfortable."""
    # Duration component: shorter is better, linear normalisation
    duration_ratio = flight.outbound_duration_minutes / _DURATION_REF_MIN
    duration_component = max(0.0, 1.0 - duration_ratio)

    # Direct flight bonus
    direct_bonus = _DIRECT_BONUS if flight.layover_count == 0 else 0.0

    # Cabin bonus
    cabin_bonus = _CABIN_BONUS.get(flight.cabin_class, 0.0)

    # Daytime arrival bonus
    arrive_hour = _parse_hour(flight.outbound_arrival_at)
    daytime_bonus = _DAYTIME_BONUS if _DAYTIME_START <= arrive_hour <= _DAYTIME_END else 0.0

    raw = duration_component + direct_bonus + cabin_bonus + daytime_bonus
    return max(0.0, min(1.0, raw))


def _parse_hour(iso_dt: str) -> int:
    try:
        time_part = iso_dt.split("T")[1]
        return int(time_part[:2])
    except (IndexError, ValueError):
        return 12
