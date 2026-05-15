"""Aviasales provider adapter — Travelpayouts Data API v3.

Docs: https://travelpayouts.github.io/aviasales-api/

Endpoint: GET /aviasales/v3/prices_for_dates
Auth: x-access-token header (or token query param)
Response currency: controlled by the `currency` query param (default: rub).
We always request INR so price_inr fields are already in the right unit.

Error handling:
  - 429 / 5xx / timeouts: automatic exponential backoff (3 attempts, 1s/2s/4s delays)
  - other 4xx: raise AviasalesClientError immediately
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

_BASE_URL = "https://api.travelpayouts.com"
_PRICES_PATH = "/aviasales/v3/prices_for_dates"
_HTTP_OK = 200
_HTTP_RATE_LIMIT = 429
_HTTP_SERVER_ERROR_FLOOR = 500

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt


class AviasalesError(Exception):
    pass


class AviasalesRateLimitError(AviasalesError):
    pass


class AviasalesServerError(AviasalesError):
    pass


class AviasalesClientError(AviasalesError):
    pass


async def _call_with_retry(
    client: httpx.AsyncClient, path: str, params: dict[str, Any]
) -> dict[str, Any]:
    """GET with exponential backoff on 429 and 5xx."""
    delay = _RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = await client.get(path, params=params)
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES:
                raise
            await asyncio.sleep(delay)
            delay *= 2
            continue

        if response.status_code == _HTTP_OK:
            return response.json()  # type: ignore[no-any-return]

        # Retryable: 429 and 5xx
        is_retryable = (
            response.status_code == _HTTP_RATE_LIMIT
            or response.status_code >= _HTTP_SERVER_ERROR_FLOOR
        )
        if is_retryable:
            if attempt == _MAX_RETRIES:
                _raise_for_status(response)  # raises the appropriate error
            await asyncio.sleep(delay)
            delay *= 2
            continue

        # Non-retryable 4xx
        _raise_for_status(response)

    # Should not reach here
    if last_exc:
        raise last_exc
    msg = "Unexpected retry loop exit"
    raise AviasalesError(msg)


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

        data = await _call_with_retry(self._client, _PRICES_PATH, params)
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
