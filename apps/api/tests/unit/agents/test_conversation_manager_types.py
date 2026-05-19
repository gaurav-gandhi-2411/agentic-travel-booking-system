"""Pydantic validation tests for conversation_manager_types.py."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from travel_agent.agents.conversation_manager_types import (
    ConversationAction,
    ConversationManagerOutput,
    NoOpArgs,
    RefineArgs,
    ReplanArgs,
)

# ── ConversationAction ────────────────────────────────────────────────────────


def test_conversation_action_values() -> None:
    assert ConversationAction.REFINE == "refine"
    assert ConversationAction.REPLAN == "replan"
    assert ConversationAction.NO_OP == "no_op"


# ── RefineArgs ────────────────────────────────────────────────────────────────


def test_refine_args_defaults() -> None:
    args = RefineArgs()
    assert args.price_max_inr is None
    assert args.price_min_inr is None
    assert args.direct_only is False
    assert args.max_layover_count is None
    assert args.departure_window is None
    assert args.sort_by == "price"
    assert args.clear_filters is False


def test_refine_args_price_max_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        RefineArgs(price_max_inr=-1)


def test_refine_args_price_min_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        RefineArgs(price_min_inr=-500)


def test_refine_args_max_layover_count_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        RefineArgs(max_layover_count=-1)


def test_refine_args_departure_window_enum() -> None:
    for val in ("morning", "afternoon", "evening", "night"):
        args = RefineArgs(departure_window=val)  # type: ignore[arg-type]
        assert args.departure_window == val


def test_refine_args_departure_window_invalid() -> None:
    with pytest.raises(ValidationError):
        RefineArgs(departure_window="midnight")  # type: ignore[arg-type]


def test_refine_args_sort_by_enum() -> None:
    for val in ("price", "duration", "stops"):
        args = RefineArgs(sort_by=val)  # type: ignore[arg-type]
        assert args.sort_by == val


def test_refine_args_sort_by_invalid() -> None:
    with pytest.raises(ValidationError):
        RefineArgs(sort_by="airline")  # type: ignore[arg-type]


def test_refine_args_full_valid() -> None:
    args = RefineArgs(
        price_max_inr=25000,
        direct_only=True,
        departure_window="morning",
        sort_by="duration",
    )
    assert args.price_max_inr == 25000
    assert args.direct_only is True
    assert args.departure_window == "morning"
    assert args.sort_by == "duration"


# ── ReplanArgs ────────────────────────────────────────────────────────────────


def test_replan_args_all_none_by_default() -> None:
    args = ReplanArgs()
    assert args.origin_iata is None
    assert args.destination_iata is None
    assert args.departure_window_start is None
    assert args.flexible_dates is None
    assert args.preferred_airlines is None
    assert args.budget_max_inr is None


def test_replan_args_iata_must_be_3_chars() -> None:
    with pytest.raises(ValidationError):
        ReplanArgs(origin_iata="BM")  # too short
    with pytest.raises(ValidationError):
        ReplanArgs(destination_iata="BOMX")  # too long


def test_replan_args_iata_exactly_3_chars_accepted() -> None:
    args = ReplanArgs(origin_iata="BOM", destination_iata="SIN")
    assert args.origin_iata == "BOM"
    assert args.destination_iata == "SIN"


def test_replan_args_budget_max_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        ReplanArgs(budget_max_inr=-1)


def test_replan_args_dates_accepted() -> None:
    args = ReplanArgs(
        departure_window_start=date(2026, 12, 1),
        departure_window_end=date(2026, 12, 31),
    )
    assert args.departure_window_start == date(2026, 12, 1)
    assert args.departure_window_end == date(2026, 12, 31)


def test_replan_args_preferred_airlines_list() -> None:
    args = ReplanArgs(preferred_airlines=["AI", "EK"])
    assert args.preferred_airlines == ["AI", "EK"]


# ── NoOpArgs ──────────────────────────────────────────────────────────────────


def test_no_op_args_explanation_required() -> None:
    with pytest.raises(ValidationError):
        NoOpArgs()  # type: ignore[call-arg]


def test_no_op_args_explanation_too_short() -> None:
    with pytest.raises(ValidationError):
        NoOpArgs(explanation="Too short")  # < 20 chars


def test_no_op_args_explanation_too_long() -> None:
    with pytest.raises(ValidationError):
        NoOpArgs(explanation="x" * 201)  # > 200 chars


def test_no_op_args_explanation_at_boundaries() -> None:
    short_ok = NoOpArgs(explanation="x" * 20)
    assert len(short_ok.explanation) == 20
    long_ok = NoOpArgs(explanation="x" * 200)
    assert len(long_ok.explanation) == 200


# ── ConversationManagerOutput — exactly-one-args invariant ───────────────────


def test_output_zero_args_raises() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ConversationManagerOutput(action=ConversationAction.REFINE)


def test_output_two_args_raises() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ConversationManagerOutput(
            action=ConversationAction.REFINE,
            refine_args=RefineArgs(),
            replan_args=ReplanArgs(),
        )


def test_output_three_args_raises() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ConversationManagerOutput(
            action=ConversationAction.REFINE,
            refine_args=RefineArgs(),
            replan_args=ReplanArgs(),
            no_op_args=NoOpArgs(explanation="I help refine flight searches."),
        )


def test_output_refine_valid() -> None:
    out = ConversationManagerOutput(
        action=ConversationAction.REFINE,
        refine_args=RefineArgs(direct_only=True),
        args_summary="Direct flights only",
    )
    assert out.action == ConversationAction.REFINE
    assert out.refine_args is not None
    assert out.replan_args is None
    assert out.no_op_args is None


def test_output_replan_valid() -> None:
    out = ConversationManagerOutput(
        action=ConversationAction.REPLAN,
        replan_args=ReplanArgs(destination_iata="SIN"),
        args_summary="Searching Delhi to Singapore",
    )
    assert out.action == ConversationAction.REPLAN
    assert out.replan_args is not None
    assert out.refine_args is None


def test_output_no_op_valid() -> None:
    out = ConversationManagerOutput(
        action=ConversationAction.NO_OP,
        no_op_args=NoOpArgs(explanation="I help refine flight searches. Try filtering!"),
    )
    assert out.action == ConversationAction.NO_OP
    assert out.no_op_args is not None
