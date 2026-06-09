"""Shared InventoryProvider lifecycle contract.

Both AviasalesAdapter (flights) and HotellookAdapter (hotels) satisfy this
protocol structurally — no inheritance required.

Normalization position: this contract is agnostic. It defines lifecycle
(close) and a shared error hierarchy only. Return types for search methods
are left to each vertical adapter; the existing flight-normalization split
(raw dicts in adapter, typed FlightOption in FlightHunterAgent) is preserved.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class InventoryProviderError(Exception):
    """Base exception for all inventory provider errors."""


class InventoryRateLimitError(InventoryProviderError):
    """Provider returned HTTP 429 or signaled rate limiting."""


class InventoryServerError(InventoryProviderError):
    """Provider returned HTTP 5xx."""


class InventoryClientError(InventoryProviderError):
    """Provider returned HTTP 4xx (non-429)."""


@runtime_checkable
class InventoryProvider(Protocol):
    """Lifecycle contract shared by all inventory adapters.

    Defines only async cleanup. Search method signatures and return types
    are vertical-specific and NOT part of this base protocol.
    """

    async def close(self) -> None:
        """Release underlying HTTP client / connection pool."""
        ...
