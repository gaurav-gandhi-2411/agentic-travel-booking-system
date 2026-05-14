"""Value score for a flight option.

Higher → more financially attractive for the traveler.
The score is in [0.0, 1.0] and is RELATIVE — only meaningful when
compared across options in the same search, not across searches.

Factors:
  - price_inr: sole driver — cheaper is better (sigmoid normalised to _PRICE_REF_INR)

Experience penalties (layovers, red-eye) are intentionally absent here;
they live in experience_score so the Pareto axes remain independent.
This lets cheap-but-uncomfortable flights win "best value" while
comfortable-but-pricier flights win "best experience".
"""

from __future__ import annotations

import math

from travel_agent.coordinator.state import FlightOption

# Price reference for normalisation: roughly the 95th-pctile for Indian
# international routes in INR. Scores saturate gracefully above this.
_PRICE_REF_INR = 200_000


def value_score(flight: FlightOption) -> float:
    """Return a value score in [0.0, 1.0]; higher is more value-for-money."""
    price_ratio = flight.price_inr / _PRICE_REF_INR
    return max(0.0, min(1.0, 1.0 - (1.0 / (1.0 + math.exp(-8 * (price_ratio - 0.4))))))
