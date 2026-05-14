"""Aviasales provider adapter — Travelpayouts Data API v3.

Docs: https://travelpayouts.github.io/aviasales-api/

Endpoint: GET /aviasales/v3/prices_for_dates
Auth: x-access-token header (or token query param)
Response currency: controlled by the `currency` query param (default: rub).
We always request INR so price_inr fields are already in the right unit.

Error handling:
  - 429: raise AviasalesRateLimitError (caller should back off)
  - 5xx: raise AviasalesServerError (caller should retry with backoff)
  - other 4xx: raise AviasalesClientError
"""
from __future__ import annotations

import os
from typing import Any

import httpx

_BASE_URL = "https://api.travelpayouts.com"
_PRICES_PATH = "/aviasales/v3/prices_for_dates"
_HTTP_OK = 200
_HTTP_RATE_LIMIT = 429
_HTTP_SERVER_ERROR_FLOOR = 500


class AviasalesError(Exception):
    pass


class AviasalesRateLimitError(AviasalesError):
    pass


class AviasalesServerError(AviasalesError):
    pass


class AviasalesClientError(AviasalesError):
    pass


class AviasalesAdapter:
    """Async thin wrapper over the Travelpayouts Aviasales Data API."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key if api_key is not None else os.environ.get("AVIASALES_API_KEY", "")
        if not key:
            msg = (
                "AviasalesAdapter requires AVIASALES_API_KEY. "
                "Set the env var or pass api_key= to the constructor."
            )
            raise RuntimeError(msg)
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"x-access-token": key},
            timeout=10.0,
        )

    async def get_flights(
        self,
        origin: str,
        destination: str,
        departure_at: str,
        *,
        return_at: str | None = None,
        currency: str = "inr",
        sorting: str = "price",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search for flights. Returns a list of raw flight dicts."""
        params: dict[str, str | int] = {
            "origin": origin,
            "destination": destination,
            "departure_at": departure_at,
            "currency": currency,
            "sorting": sorting,
            "limit": limit,
            "unique": "false",
        }
        if return_at is not None:
            params["return_at"] = return_at

        response = await self._client.get(_PRICES_PATH, params=params)
        _raise_for_status(response)
        data: dict[str, Any] = response.json()
        return list(data.get("data", []))

    async def close(self) -> None:
        await self._client.aclose()


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == _HTTP_OK:
        return
    if response.status_code == _HTTP_RATE_LIMIT:
        msg = f"Aviasales rate limit (429): {response.text[:200]}"
        raise AviasalesRateLimitError(msg)
    if response.status_code >= _HTTP_SERVER_ERROR_FLOOR:
        msg = f"Aviasales server error ({response.status_code}): {response.text[:200]}"
        raise AviasalesServerError(msg)
    msg = f"Aviasales client error ({response.status_code}): {response.text[:200]}"
    raise AviasalesClientError(msg)
