"""Shared window-generation logic used by streaming.py.

Extracted from coordinator.py and streaming.py (both had identical copies).
"""

from __future__ import annotations

from datetime import timedelta

from travel_agent.coordinator.constants import MAX_WINDOWS, WINDOW_SIZE_DAYS
from travel_agent.coordinator.state import TravelIntent, Window


def generate_windows(intent: TravelIntent) -> list[Window]:
    """Generate up to MAX_WINDOWS non-overlapping WINDOW_SIZE_DAYS-wide buckets."""
    windows: list[Window] = []
    current = intent.earliest_departure
    while current <= intent.latest_departure and len(windows) < MAX_WINDOWS:
        windows.append(
            Window(
                start_date=current,
                end_date=current + timedelta(days=WINDOW_SIZE_DAYS - 1),
            )
        )
        current += timedelta(days=WINDOW_SIZE_DAYS)
    return windows
