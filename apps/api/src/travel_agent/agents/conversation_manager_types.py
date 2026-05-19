"""Pydantic types for ConversationManagerAgent structured output.

The ConversationManagerOutput is what the agent parses from the LLM tool-call
response.  The @model_validator enforces the exactly-one-args invariant at
parse time — one of refine_args, replan_args, or no_op_args must be populated.

The tool JSON Schema is derived from ConversationManagerOutput.model_json_schema()
in conversation_manager.py so it stays in sync with these models automatically.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class ConversationAction(StrEnum):
    REFINE = "refine"
    REPLAN = "replan"
    NO_OP = "no_op"


class RefineArgs(BaseModel):
    """Filter/sort spec to apply to the cached flight pool."""

    price_max_inr: Annotated[int, Field(ge=0)] | None = None
    price_min_inr: Annotated[int, Field(ge=0)] | None = None
    direct_only: bool = False
    max_layover_count: Annotated[int, Field(ge=0)] | None = None
    departure_window: Literal["morning", "afternoon", "evening", "night"] | None = None
    sort_by: Literal["price", "duration", "stops"] = "price"
    clear_filters: bool = False


class ReplanArgs(BaseModel):
    """Partial TravelIntent update to trigger a new search.

    Null fields inherit from the current cached intent. The /refine route
    handler (PR 2) is responsible for merging non-null fields into the
    existing TravelIntent before re-running stream_search.
    """

    origin_iata: Annotated[str, Field(min_length=3, max_length=3)] | None = None
    destination_iata: Annotated[str, Field(min_length=3, max_length=3)] | None = None
    departure_window_start: date | None = None
    departure_window_end: date | None = None
    flexible_dates: bool | None = None
    preferred_airlines: list[str] | None = None
    budget_max_inr: Annotated[int, Field(ge=0)] | None = None


class NoOpArgs(BaseModel):
    """Polite redirect for off-topic input.

    The explanation string acknowledges the input and redirects to flight
    refinement.  Length bounds keep it terse (20 chars min) and prevent
    runaway generation (200 chars max).
    """

    explanation: str = Field(..., min_length=20, max_length=200)


class ConversationManagerOutput(BaseModel):
    """Structured output of one ConversationManagerAgent call.

    Exactly one of refine_args / replan_args / no_op_args must be set.
    This invariant is enforced by the model_validator and mirrors the
    action enum: action=REFINE implies refine_args is set, etc.

    args_summary is a human-readable one-liner for UI display (e.g. "Direct
    flights only, under ₹25,000"). Empty string for NO_OP — the explanation
    field on no_op_args is the user-facing text in that case.
    """

    action: ConversationAction
    refine_args: RefineArgs | None = None

    @field_validator("action", mode="before")
    @classmethod
    def normalise_action_case(cls, v: object) -> object:
        if isinstance(v, str):
            return v.lower()
        return v

    replan_args: ReplanArgs | None = None
    no_op_args: NoOpArgs | None = None
    args_summary: str = Field(
        default="",
        max_length=120,
        description=(
            "Human-readable one-line summary of the classified action for UI display. "
            "Non-empty for REFINE and REPLAN. Empty string for NO_OP."
        ),
    )

    @model_validator(mode="after")
    def exactly_one_args(self) -> Self:
        populated = sum(
            arg is not None for arg in [self.refine_args, self.replan_args, self.no_op_args]
        )
        if populated != 1:
            msg = (
                f"exactly one of refine_args/replan_args/no_op_args must be set; "
                f"got {populated} populated"
            )
            raise ValueError(msg)
        needs_summary = self.action in (ConversationAction.REFINE, ConversationAction.REPLAN)
        if needs_summary and not self.args_summary:
            msg = f"args_summary must be non-empty for {self.action} actions"
            raise ValueError(msg)
        return self
