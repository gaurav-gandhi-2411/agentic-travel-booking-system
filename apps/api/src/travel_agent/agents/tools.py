"""Tool schemas for all three extraction agents.

Each ToolDefinition here is the contract between the prompt and the
coordinator's state models.  Change the schema first; update the prompt
second.  Never let prompt engineering drift the schema silently.

References: agents/prompts/{planner,flight_hunter,hotel_hunter}_system.txt
"""
from __future__ import annotations

from travel_agent.llm.base import ToolDefinition

# ── PlannerAgent ──────────────────────────────────────────────────────────────

EXTRACT_TRAVEL_INTENT = ToolDefinition(
    name="extract_travel_intent",
    description=(
        "Extract structured travel intent from a natural-language user query. "
        "Resolve relative dates against today's date (provided in system prompt). "
        "Map city names to IATA codes using the reference table in the system prompt. "
        "Use null for fields not mentioned or inferable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "origin_iata": {
                "type": "string",
                "pattern": "^[A-Z]{3}$",
                "description": "3-letter IATA code for origin airport (e.g. BOM for Mumbai).",
            },
            "destination_iata": {
                "type": "string",
                "pattern": "^([A-Z]{3}|ANY)$",
                "description": (
                    "3-letter IATA code for destination, or the literal string 'ANY' "
                    "when the user is flexible on destination."
                ),
            },
            "earliest_departure": {
                "type": "string",
                "format": "date",
                "description": "Earliest acceptable outbound departure date (YYYY-MM-DD).",
            },
            "latest_departure": {
                "type": "string",
                "format": "date",
                "description": (
                    "Latest acceptable outbound departure date (YYYY-MM-DD). "
                    "Must be at least 1 day after earliest_departure. "
                    "Set to earliest_departure + 30 days when the user gives "
                    "a single date or a narrow window."
                ),
            },
            "trip_duration_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 90,
                "description": "Duration of stay at destination in nights (default 7).",
            },
            "traveler_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 9,
                "description": "Total number of travelers including children (default 1).",
            },
            "cabin_class": {
                "type": "string",
                "enum": ["economy", "premium_economy", "business", "first"],
                "description": "Cabin class; default economy.",
            },
            "budget_inr": {
                "type": ["integer", "null"],
                "minimum": 0,
                "description": (
                    "Total trip budget in INR across all travelers. "
                    "Convert from other currencies using approximate rates. "
                    "Null if not mentioned."
                ),
            },
            "hotel_min_stars": {
                "type": "number",
                "minimum": 1.0,
                "maximum": 5.0,
                "description": "Minimum hotel star rating; default 3.0.",
            },
            "hotel_location_hint": {
                "type": ["string", "null"],
                "description": "Neighbourhood or location preference if mentioned.",
            },
            "trip_type": {
                "type": "string",
                "enum": ["one_way", "round_trip"],
                "description": "Default round_trip unless user explicitly says one-way.",
            },
            "airline_preference": {
                "type": ["string", "null"],
                "description": "Preferred airline name or IATA code if stated; else null.",
            },
            "departure_time_constraint": {
                "type": ["string", "null"],
                "description": (
                    "Time-of-day preference, e.g. 'no red-eyes', 'morning flights only'. "
                    "Null if not mentioned."
                ),
            },
            "raw_query": {
                "type": "string",
                "description": "The original user query verbatim — do not modify.",
            },
        },
        "required": [
            "origin_iata",
            "destination_iata",
            "earliest_departure",
            "latest_departure",
            "trip_duration_days",
            "traveler_count",
            "cabin_class",
            "trip_type",
            "raw_query",
        ],
        "additionalProperties": False,
    },
)

# ── FlightHunterAgent ─────────────────────────────────────────────────────────

EXTRACT_FLIGHT_OPTIONS = ToolDefinition(
    name="extract_flight_options",
    description=(
        "Extract a list of normalised flight options from raw provider JSON. "
        "Return every distinct itinerary in the response; omit duplicates."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "flights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "airline_code": {
                            "type": "string",
                            "pattern": "^[A-Z0-9]{2,3}$",
                            "description": "2-3 char IATA airline code.",
                        },
                        "flight_number": {
                            "type": "string",
                            "description": "Full flight number, e.g. 'AI-142'.",
                        },
                        "cabin_class": {
                            "type": "string",
                            "enum": ["economy", "premium_economy", "business", "first"],
                        },
                        "price_inr": {
                            "type": "integer",
                            "minimum": 1000,
                            "description": "Total one-way or round-trip price in INR.",
                        },
                        "outbound_departure_at": {
                            "type": "string",
                            "format": "date-time",
                            "description": "ISO 8601 outbound departure timestamp.",
                        },
                        "outbound_arrival_at": {
                            "type": "string",
                            "format": "date-time",
                        },
                        "outbound_duration_minutes": {
                            "type": "integer",
                            "minimum": 30,
                        },
                        "layover_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 5,
                        },
                        "return_departure_at": {
                            "type": ["string", "null"],
                            "format": "date-time",
                        },
                        "return_arrival_at": {
                            "type": ["string", "null"],
                            "format": "date-time",
                        },
                        "return_duration_minutes": {
                            "type": ["integer", "null"],
                            "minimum": 30,
                        },
                        "is_refundable": {"type": "boolean"},
                    },
                    "required": [
                        "airline_code",
                        "flight_number",
                        "cabin_class",
                        "price_inr",
                        "outbound_departure_at",
                        "outbound_arrival_at",
                        "outbound_duration_minutes",
                        "layover_count",
                        "is_refundable",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["flights"],
        "additionalProperties": False,
    },
)

# ── HotelHunterAgent ──────────────────────────────────────────────────────────

EXTRACT_HOTEL_OPTIONS = ToolDefinition(
    name="extract_hotel_options",
    description=(
        "Extract a list of normalised hotel options from raw provider JSON. "
        "Include every distinct property in the response."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "hotels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "city": {"type": "string"},
                        "stars": {
                            "type": "number",
                            "minimum": 1.0,
                            "maximum": 5.0,
                        },
                        "review_score": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 10.0,
                        },
                        "price_per_night_inr": {
                            "type": "integer",
                            "minimum": 500,
                        },
                        "location_description": {"type": "string"},
                        "is_refundable": {"type": "boolean"},
                    },
                    "required": [
                        "name",
                        "city",
                        "stars",
                        "review_score",
                        "price_per_night_inr",
                        "is_refundable",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["hotels"],
        "additionalProperties": False,
    },
)
