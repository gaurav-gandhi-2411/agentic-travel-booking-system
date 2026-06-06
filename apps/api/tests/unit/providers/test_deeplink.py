"""Unit tests for the Aviasales affiliate deep-link builder."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from travel_agent.providers.aviasales.deeplink import build_deeplink

_MARKER = "12345"


def _parse(url: str) -> tuple[str, dict[str, list[str]]]:
    """Return (path, query_params) from a URL."""
    parsed = urlparse(url)
    return parsed.path, parse_qs(parsed.query)


def test_base_url_is_aviasales() -> None:
    url = build_deeplink(
        raw_link="/search/BOM0106CDG08062026",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
    )
    assert url.startswith("https://www.aviasales.com")


def test_raw_link_used_when_present() -> None:
    url = build_deeplink(
        raw_link="/search/BOM0106CDG08062026",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
    )
    path, _ = _parse(url)
    assert path == "/search/BOM0106CDG08062026"


def test_partner_marker_in_query() -> None:
    url = build_deeplink(
        raw_link="/search/BOM0106CDG08062026",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
    )
    _, qs = _parse(url)
    assert qs["marker"] == [_MARKER]


def test_archetype_label_appended_to_marker() -> None:
    url = build_deeplink(
        raw_link="/search/BOM0106CDG08062026",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
        archetype_label="best-value",
    )
    _, qs = _parse(url)
    assert qs["marker"] == [f"{_MARKER}.best-value"]


def test_utm_params_present() -> None:
    url = build_deeplink(
        raw_link="/search/BOM0106CDG08062026",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
    )
    _, qs = _parse(url)
    assert qs["utm_source"] == ["dealhunter"]
    assert qs["utm_medium"] == ["affiliate"]
    assert "utm_campaign" in qs


def test_utm_campaign_override() -> None:
    url = build_deeplink(
        raw_link=None,
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
        utm_campaign="may_launch",
    )
    _, qs = _parse(url)
    assert qs["utm_campaign"] == ["may_launch"]


def test_fallback_path_when_raw_link_none() -> None:
    url = build_deeplink(
        raw_link=None,
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
    )
    path, _ = _parse(url)
    assert "BOM" in path
    assert "CDG" in path


def test_fallback_path_encodes_date_correctly() -> None:
    url = build_deeplink(
        raw_link=None,
        origin_iata="NRT",
        destination_iata="DPS",
        departure_date="2026-08-15",
        partner_marker=_MARKER,
    )
    path, _ = _parse(url)
    # Expect DDMM in path: 15 August → 1508
    assert "1508" in path
    assert "NRT" in path
    assert "DPS" in path


def test_fallback_path_empty_raw_link() -> None:
    url = build_deeplink(
        raw_link="",
        origin_iata="BOM",
        destination_iata="NRT",
        departure_date="2026-07-01",
        partner_marker=_MARKER,
    )
    path, _ = _parse(url)
    # Empty string treated same as None
    assert "BOM" in path
    assert "NRT" in path


def test_raw_link_without_leading_slash() -> None:
    url = build_deeplink(
        raw_link="search/BOM0106CDG08062026",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
    )
    path, _ = _parse(url)
    assert path.startswith("/search/")


def test_one_way_no_return_leg() -> None:
    url = build_deeplink(
        raw_link="/search/BOM0108NRT",
        origin_iata="BOM",
        destination_iata="NRT",
        departure_date="2026-08-01",
        partner_marker=_MARKER,
    )
    assert "BOM" in url
    assert "NRT" in url
    # No crash for one-way (no return date in input)


def test_no_archetype_label_no_dot_in_marker() -> None:
    url = build_deeplink(
        raw_link="/search/BOM0106CDG08062026",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
        archetype_label="",
    )
    _, qs = _parse(url)
    assert "." not in qs["marker"][0]


# ---------------------------------------------------------------------------
# Separator branch: raw_link with vs without pre-existing query params
# ---------------------------------------------------------------------------


def test_raw_link_with_existing_query_string_has_single_question_mark() -> None:
    """Aviasales API raw_link carries ?t=... query params — must join with &, not ?.

    Regression: build_deeplink previously emitted two ? chars, making the marker
    unparseable by Travelpayouts and breaking affiliate attribution.
    """
    raw_link_with_qs = "/search/BOM1507CDG29071?t=EY178411440&expected_price=58816"
    url = build_deeplink(
        raw_link=raw_link_with_qs,
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-07-15",
        partner_marker=_MARKER,
        archetype_label="best-value",
    )
    assert url.count("?") == 1
    _, qs = _parse(url)
    assert qs["marker"] == [f"{_MARKER}.best-value"]


def test_raw_link_without_query_string_uses_question_mark_separator() -> None:
    """raw_link with no existing query params still gets the correct ? separator."""
    url = build_deeplink(
        raw_link="/search/BOM0106CDG",
        origin_iata="BOM",
        destination_iata="CDG",
        departure_date="2026-06-01",
        partner_marker=_MARKER,
    )
    assert url.count("?") == 1
    _, qs = _parse(url)
    assert qs["marker"] == [_MARKER]
