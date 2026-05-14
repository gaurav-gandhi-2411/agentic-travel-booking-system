"""Affiliate deep-link builder for Aviasales / Travelpayouts.

Revenue path: every archetype card in the UI carries one of these URLs.
The partner marker (AVIASALES_PARTNER_ID) is embedded so Travelpayouts
attributes the session to DealHunter and pays commission on completed bookings.

URL shape:
    https://www.aviasales.com{raw_link}?marker={marker}&utm_source=dealhunter&...

The `raw_link` comes from the Aviasales API response field "link",
e.g. "/search/BOM0106CDG08062026".  When the raw link is absent (synthetic
provider or future adapters), we construct a fallback from IATA codes and date.
"""
from __future__ import annotations

from urllib.parse import urlencode

_AVIASALES_BASE = "https://www.aviasales.com"


def build_deeplink(
    *,
    raw_link: str | None,
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    partner_marker: str,
    archetype_label: str = "",
    utm_campaign: str = "demo",
) -> str:
    """Return an Aviasales affiliate search URL.

    Args:
        raw_link: the "link" value from Aviasales API, e.g. "/search/BOM0106CDG08062026".
                  If None or empty, a fallback path is constructed.
        origin_iata: 3-letter origin IATA code.
        destination_iata: 3-letter destination IATA code.
        departure_date: ISO-8601 date string "YYYY-MM-DD".
        partner_marker: Travelpayouts partner ID (AVIASALES_PARTNER_ID env var).
        archetype_label: optional sub-ID suffix (e.g. "best-value"); appended to marker.
        utm_campaign: UTM campaign tag.
    """
    if raw_link:
        path = raw_link if raw_link.startswith("/") else f"/{raw_link}"
    else:
        # Construct /search/AAADDMMYYYBBBB fallback
        try:
            parts = departure_date.split("-")
            day_month = parts[2] + parts[1]  # DDMM
            year = parts[0]  # YYYY
            path = f"/search/{origin_iata}{day_month}{year}{destination_iata}"
        except (IndexError, AttributeError):
            path = f"/search/{origin_iata}01{destination_iata}"

    marker = f"{partner_marker}.{archetype_label}" if archetype_label else partner_marker
    params = urlencode(
        {
            "marker": marker,
            "utm_source": "dealhunter",
            "utm_medium": "affiliate",
            "utm_campaign": utm_campaign,
        }
    )
    return f"{_AVIASALES_BASE}{path}?{params}"
