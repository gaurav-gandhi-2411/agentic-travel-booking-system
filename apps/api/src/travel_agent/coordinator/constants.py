"""Coordinator runtime constants loaded from config/coordinator.yaml.

Phase D will tune these values — centralised here so callers need no updates.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "coordinator.yaml"


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    with _CONFIG_PATH.open() as f:
        data: dict[str, Any] = yaml.safe_load(f)
        return data


def _cfg() -> dict[str, Any]:
    return _load_config()


# Call budget
FLIGHT_CALLS_MAX: int = _cfg()["call_budget"]["flight_calls_max"]
HOTEL_CALLS_MAX: int = _cfg()["call_budget"]["hotel_calls_max"]
LLM_CALLS_MAX: int = _cfg()["call_budget"]["llm_calls_max"]

# Window search (ADR-0005)
HORIZON_DAYS: int = _cfg()["window_search"]["horizon_days"]
WINDOW_SIZE_DAYS: int = _cfg()["window_search"]["window_size_days"]
MAX_WINDOWS: int = _cfg()["window_search"]["max_windows"]

# Destination city mapping (extend before adding new routes)
IATA_TO_CITY: dict[str, str] = _cfg()["destination_cities"]
