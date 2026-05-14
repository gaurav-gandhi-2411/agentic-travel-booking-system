"""Shared state model for the coordinator and all agents.

RequestState is the single source of truth for one user request. It flows through
the coordinator -> agents -> back to coordinator, accumulating results at each step.
The coordinator is the only writer of top-level fields; agents receive the state,
perform their work, and return a mutated copy.

All models use Pydantic v2 for validation and serialization. RequestState serializes
to/from Redis (active sessions) and Postgres (conversation history).

References: ADR-0001 (coordinator pattern), ADR-0005 (window search), ADR-0006 (scoring).
"""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# ── enumerations ──────────────────────────────────────────────────────────────


class TripType(StrEnum):
    ONE_WAY = "one_way"
    ROUND_TRIP = "round_trip"


class CabinClass(StrEnum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class Archetype(StrEnum):
    BEST_VALUE = "best_value"
    BEST_EXPERIENCE = "best_experience"


class BookingPhase(StrEnum):
    IDLE = "idle"
    LOCKED = "locked"  # offer locked, awaiting user confirmation
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # hold timer expired without confirmation


class CoordinatorPhase(StrEnum):
    PLANNING = "planning"
    SEARCHING = "searching"
    OPTIMIZING = "optimizing"
    PRESENTING = "presenting"
    BOOKING = "booking"
    DONE = "done"
    ERROR = "error"


# ── intent ────────────────────────────────────────────────────────────────────


class TravelIntent(BaseModel):
    """Structured representation of user intent after PlannerAgent parsing."""

    origin_iata: str
    destination_iata: str
    # The 30-day search horizon: earliest_departure -> latest_departure
    earliest_departure: date
    latest_departure: date
    trip_duration_days: int = 7
    traveler_count: int = 1
    cabin_class: CabinClass = CabinClass.ECONOMY
    budget_inr: int | None = None
    hotel_min_stars: float = 3.0
    hotel_location_hint: str | None = None
    trip_type: TripType = TripType.ROUND_TRIP
    departure_time_constraint: str | None = None  # e.g. "no red-eyes", "mornings only"
    raw_query: str = ""


# ── search primitives ─────────────────────────────────────────────────────────


class Window(BaseModel):
    """A 7-day candidate travel window."""

    start_date: date
    end_date: date
    interim_score: float = 0.0  # set during Stage 1/2 coarse sweep


class FlightOption(BaseModel):
    """Normalised flight result from a provider adapter."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    window: Window
    provider: str  # "aviasales" | "synthetic" | "amadeus" | "duffel"
    origin_iata: str
    destination_iata: str
    outbound_departure_at: str  # ISO 8601 datetime
    outbound_arrival_at: str
    return_departure_at: str | None = None
    return_arrival_at: str | None = None
    airline_code: str
    flight_number: str
    cabin_class: CabinClass
    price_inr: int
    outbound_duration_minutes: int
    return_duration_minutes: int | None = None
    layover_count: int = 0
    is_refundable: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class HotelOption(BaseModel):
    """Normalised hotel result from a provider adapter."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    window: Window
    provider: str
    name: str
    city: str
    stars: float
    review_score: float  # 0-10 scale
    price_per_night_inr: int
    total_price_inr: int  # price_per_night_inr x trip_duration_days
    location_description: str = ""
    is_refundable: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


# ── scoring & packages ────────────────────────────────────────────────────────


class Package(BaseModel):
    """A flight + hotel combination for a given window, with scoring."""

    archetype: Archetype
    flight: FlightOption
    hotel: HotelOption
    window: Window
    total_price_inr: int
    value_score: float
    experience_score: float
    explanation: str = ""  # 2-3 sentence NL explanation from OptimizerAgent


# ── booking ───────────────────────────────────────────────────────────────────


class BookingStatus(BaseModel):
    """HITL booking state machine. Managed exclusively by BookingAgent."""

    phase: BookingPhase = BookingPhase.IDLE
    selected_package: Package | None = None
    offer_lock_id: str | None = None
    hold_expires_at: str | None = None  # ISO 8601
    idempotency_key: str | None = None
    pnr: str | None = None  # returned by provider on confirm
    audit_id: UUID | None = None


# ── call budget ───────────────────────────────────────────────────────────────


class CallBudget(BaseModel):
    """Hard per-request caps on provider and LLM call counts (ADR-0001 §5.3)."""

    flight_calls_used: int = 0
    hotel_calls_used: int = 0
    llm_calls_used: int = 0
    flight_calls_max: int = 150
    hotel_calls_max: int = 100
    llm_calls_max: int = 20

    def can_call_flight(self) -> bool:
        return self.flight_calls_used < self.flight_calls_max

    def can_call_hotel(self) -> bool:
        return self.hotel_calls_used < self.hotel_calls_max

    def can_call_llm(self) -> bool:
        return self.llm_calls_used < self.llm_calls_max

    def is_degraded(self) -> bool:
        """True when any budget is exhausted — results are partial."""
        return (
            self.flight_calls_used >= self.flight_calls_max
            or self.hotel_calls_used >= self.hotel_calls_max
            or self.llm_calls_used >= self.llm_calls_max
        )


# ── top-level request state ───────────────────────────────────────────────────


class RequestState(BaseModel):
    """Single source of truth for one user request, flowing through the coordinator.

    Coordinator is the sole writer of top-level fields. Agents receive a copy,
    populate their output section, and return the updated state.
    """

    request_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = ""
    user_id: str = ""
    raw_input: str = ""

    # Set by PlannerAgent
    intent: TravelIntent | None = None

    # Set by WindowSearcher / FlightHunterAgent / HotelHunterAgent
    candidate_windows: list[Window] = Field(default_factory=list)
    flight_options: list[FlightOption] = Field(default_factory=list)
    hotel_options: list[HotelOption] = Field(default_factory=list)

    # Set by OptimizerAgent
    packages: list[Package] = Field(default_factory=list)
    best_value_package: Package | None = None
    best_experience_package: Package | None = None

    # Set by BookingAgent
    booking: BookingStatus = Field(default_factory=BookingStatus)

    # Coordinator metadata
    phase: CoordinatorPhase = CoordinatorPhase.PLANNING
    call_budget: CallBudget = Field(default_factory=CallBudget)
    errors: list[str] = Field(default_factory=list)
    is_partial: bool = False  # True when call budget exhausted mid-search
