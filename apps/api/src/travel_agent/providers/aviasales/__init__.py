"""Aviasales provider package — adapter + deep-link builder."""

from __future__ import annotations

from travel_agent.providers.aviasales.adapter import (
    AviasalesAdapter,
    AviasalesClientError,
    AviasalesError,
    AviasalesRateLimitError,
    AviasalesServerError,
)

__all__ = [
    "AviasalesAdapter",
    "AviasalesClientError",
    "AviasalesError",
    "AviasalesRateLimitError",
    "AviasalesServerError",
]
