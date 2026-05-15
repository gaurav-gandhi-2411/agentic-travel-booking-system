"""Deterministic synthetic data provider for testing and development.

Loads flight and hotel templates from providers/data/{flights,hotels}.json and
hydrates them with window-specific timestamps on each call.  All outputs are
deterministic for the same (origin, destination, window) / (city, window, nights)
inputs.

References: ADR-0013 (provider contract and statistical property guarantees).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from travel_agent.coordinator.state import CabinClass, FlightOption, HotelOption, TripType, Window

_DATA_DIR = Path(__file__).parent / "data"


@lru_cache(maxsize=1)
def _load_flights() -> list[dict[str, Any]]:
    with (_DATA_DIR / "flights.json").open() as f:
        data: dict[str, Any] = json.load(f)
        return cast(list[dict[str, Any]], data["flights"])


@lru_cache(maxsize=1)
def _load_hotels() -> list[dict[str, Any]]:
    with (_DATA_DIR / "hotels.json").open() as f:
        data: dict[str, Any] = json.load(f)
        return cast(list[dict[str, Any]], data["hotels"])


class SyntheticProvider:
    """Returns pre-defined flight and hotel options from static JSON templates."""

    def get_flights(
        self,
        origin: str,
        destination: str,
        window: Window,
        *,
        trip_type: TripType = TripType.ROUND_TRIP,
        trip_duration_days: int = 7,
    ) -> list[FlightOption]:
        templates = [
            t
            for t in _load_flights()
            if t["origin_iata"] == origin and t["destination_iata"] == destination
        ]
        options: list[FlightOption] = []
        is_one_way = trip_type == TripType.ONE_WAY
        for tmpl in templates:
            dep_hour: int = tmpl["outbound_depart_hour"]
            outbound_dep = datetime(
                window.start_date.year,
                window.start_date.month,
                window.start_date.day,
                dep_hour,
                30,
                tzinfo=UTC,
            )
            outbound_arr = outbound_dep + timedelta(minutes=tmpl["outbound_duration_minutes"])

            # One-way: no return leg, price is ~58% of round-trip base
            if is_one_way:
                price = round(tmpl["price_inr"] * 0.58)
                ret_dep_str: str | None = None
                ret_arr_str: str | None = None
                ret_dur: int | None = None
            else:
                price = tmpl["price_inr"]
                ret_date = window.start_date + timedelta(days=trip_duration_days)
                ret_dep = datetime(ret_date.year, ret_date.month, ret_date.day, 10, 0, tzinfo=UTC)
                ret_dur = tmpl.get("return_duration_minutes")
                ret_arr = (ret_dep + timedelta(minutes=ret_dur)) if ret_dur is not None else None
                ret_dep_str = ret_dep.isoformat()
                ret_arr_str = ret_arr.isoformat() if ret_arr is not None else None

            options.append(
                FlightOption(
                    id=f"{tmpl['id_prefix']}-{window.start_date.isoformat()}"
                    f"-{'ow' if is_one_way else f'rt{trip_duration_days}'}",
                    window=window,
                    provider="synthetic",
                    origin_iata=origin,
                    destination_iata=destination,
                    outbound_departure_at=outbound_dep.isoformat(),
                    outbound_arrival_at=outbound_arr.isoformat(),
                    return_departure_at=ret_dep_str,
                    return_arrival_at=ret_arr_str,
                    airline_code=tmpl["airline_code"],
                    flight_number=tmpl["flight_number"],
                    cabin_class=CabinClass(tmpl["cabin_class"]),
                    price_inr=price,
                    outbound_duration_minutes=tmpl["outbound_duration_minutes"],
                    return_duration_minutes=ret_dur,
                    layover_count=tmpl.get("layover_count", 0),
                    is_refundable=tmpl.get("is_refundable", False),
                )
            )
        return options

    def get_hotels(
        self,
        city: str,
        window: Window,
        nights: int,
        min_stars: float = 0.0,
    ) -> list[HotelOption]:
        templates = [t for t in _load_hotels() if t["city"] == city and t["stars"] >= min_stars]
        return [
            HotelOption(
                id=f"{t['id_prefix']}-{window.start_date.isoformat()}",
                window=window,
                provider="synthetic",
                name=t["name"],
                city=t["city"],
                stars=t["stars"],
                review_score=t["review_score"],
                price_per_night_inr=t["price_per_night_inr"],
                total_price_inr=t["price_per_night_inr"] * nights,
                location_description=t.get("location_description", ""),
                is_refundable=t.get("is_refundable", False),
            )
            for t in templates
        ]
