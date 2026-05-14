"""Schema validity tests for the three extraction tool definitions."""
from __future__ import annotations

from travel_agent.agents.tools import (
    EXTRACT_FLIGHT_OPTIONS,
    EXTRACT_HOTEL_OPTIONS,
    EXTRACT_TRAVEL_INTENT,
)
from travel_agent.llm.base import ToolDefinition


def test_extract_travel_intent_is_tool_definition() -> None:
    assert isinstance(EXTRACT_TRAVEL_INTENT, ToolDefinition)


def test_extract_flight_options_is_tool_definition() -> None:
    assert isinstance(EXTRACT_FLIGHT_OPTIONS, ToolDefinition)


def test_extract_hotel_options_is_tool_definition() -> None:
    assert isinstance(EXTRACT_HOTEL_OPTIONS, ToolDefinition)


def test_intent_schema_has_required_fields() -> None:
    schema = EXTRACT_TRAVEL_INTENT.input_schema
    required = set(schema["required"])
    assert "origin_iata" in required
    assert "destination_iata" in required
    assert "earliest_departure" in required
    assert "latest_departure" in required
    assert "trip_duration_days" in required
    assert "traveler_count" in required
    assert "cabin_class" in required
    assert "trip_type" in required
    assert "raw_query" in required


def test_intent_schema_cabin_class_enum() -> None:
    props = EXTRACT_TRAVEL_INTENT.input_schema["properties"]
    assert set(props["cabin_class"]["enum"]) == {
        "economy",
        "premium_economy",
        "business",
        "first",
    }


def test_intent_schema_trip_type_enum() -> None:
    props = EXTRACT_TRAVEL_INTENT.input_schema["properties"]
    assert set(props["trip_type"]["enum"]) == {"one_way", "round_trip"}


def test_intent_schema_traveler_count_bounds() -> None:
    props = EXTRACT_TRAVEL_INTENT.input_schema["properties"]
    assert props["traveler_count"]["minimum"] == 1
    assert props["traveler_count"]["maximum"] == 9


def test_intent_schema_additional_properties_false() -> None:
    assert EXTRACT_TRAVEL_INTENT.input_schema.get("additionalProperties") is False


def test_flight_schema_has_required_fields() -> None:
    items = EXTRACT_FLIGHT_OPTIONS.input_schema["properties"]["flights"]["items"]
    required = set(items["required"])
    assert "airline_code" in required
    assert "price_inr" in required
    assert "outbound_departure_at" in required
    assert "layover_count" in required


def test_hotel_schema_has_required_fields() -> None:
    items = EXTRACT_HOTEL_OPTIONS.input_schema["properties"]["hotels"]["items"]
    required = set(items["required"])
    assert "name" in required
    assert "city" in required
    assert "stars" in required
    assert "review_score" in required
    assert "price_per_night_inr" in required


def test_all_tool_names_are_distinct() -> None:
    names = {
        EXTRACT_TRAVEL_INTENT.name,
        EXTRACT_FLIGHT_OPTIONS.name,
        EXTRACT_HOTEL_OPTIONS.name,
    }
    assert len(names) == 3
