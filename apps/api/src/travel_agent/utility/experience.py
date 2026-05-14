"""Experience score for a flight option.

Higher -> better travel experience (comfort, time efficiency, convenience).

Factors:
  - Total outbound duration: shorter is better (capped contribution to prevent
    short-haul non-stop flights from saturating the score)
  - Direct flight bonus: layover_count == 0 is strongly preferred
  - Departure time quality: prime morning (07-11) > afternoon > evening > red-eye
  - Daytime arrival bonus: arriving 08-20 is more convenient
  - Cabin class bonus: business/first get a boost

Design intent: the score must spread across flights on a short-haul route like
BOM->DXB where all options may be non-stop.  A 07:00 non-stop should score
meaningfully higher than a 03:00 red-eye on the same route.
"""
from __future__ import annotations

from travel_agent.coordinator.state import CabinClass, FlightOption

_DURATION_REF_MIN = 900
# Cap the duration contribution so it does not dominate for short-haul routes.
_DURATION_MAX_COMPONENT = 0.50
_DIRECT_BONUS = 0.12
_CABIN_BONUS = {
    CabinClass.ECONOMY: 0.0,
    CabinClass.PREMIUM_ECONOMY: 0.05,
    CabinClass.BUSINESS: 0.12,
    CabinClass.FIRST: 0.15,
}
_DAYTIME_ARRIVAL_BONUS = 0.04  # arrival between 08:00 and 20:59
_DAYTIME_START = 8
_DAYTIME_END = 20

# Each entry: (start_hour_inclusive, end_hour_inclusive, quality_bonus)
# Hours not covered (00-05) return 0.0 (red-eye departure, worst).
_DEPARTURE_QUALITY_WINDOWS: tuple[tuple[int, int, float], ...] = (
    (7, 11, 0.15),   # prime morning: most convenient
    (12, 16, 0.10),  # afternoon: good
    (17, 20, 0.07),  # early evening: acceptable
    (6, 6, 0.05),    # very early: slight inconvenience
    (21, 23, 0.03),  # late night: not great
)


def _departure_quality(hour: int) -> float:
    """Return a departure quality bonus (0.0-0.15) based on departure hour."""
    for start, end, bonus in _DEPARTURE_QUALITY_WINDOWS:
        if start <= hour <= end:
            return bonus
    return 0.0


def experience_score(flight: FlightOption) -> float:
    """Return an experience score in [0.0, 1.0]; higher is more comfortable."""
    duration_component = min(
        _DURATION_MAX_COMPONENT,
        max(0.0, 1.0 - flight.outbound_duration_minutes / _DURATION_REF_MIN),
    )

    direct_bonus = _DIRECT_BONUS if flight.layover_count == 0 else 0.0

    cabin_bonus = _CABIN_BONUS.get(flight.cabin_class, 0.0)

    arrive_hour = _parse_hour(flight.outbound_arrival_at)
    daytime_bonus = _DAYTIME_ARRIVAL_BONUS if _DAYTIME_START <= arrive_hour <= _DAYTIME_END else 0.0

    depart_hour = _parse_hour(flight.outbound_departure_at)
    qual = _departure_quality(depart_hour)

    raw = duration_component + direct_bonus + cabin_bonus + daytime_bonus + qual
    return max(0.0, min(1.0, raw))


def _parse_hour(iso_dt: str) -> int:
    try:
        time_part = iso_dt.split("T")[1]
        return int(time_part[:2])
    except (IndexError, ValueError):
        return 12
