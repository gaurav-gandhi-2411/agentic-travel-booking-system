"""Unit tests for the conversation SSE event types added in Phase 2C.4 PR 2.

Verifies that StreamEventType values serialize to the expected strings and that
the payload shapes for the three new conversation events are well-formed.
"""

from __future__ import annotations

import json

from travel_agent.coordinator.streaming import StreamEventType

# ── StreamEventType enum values ───────────────────────────────────────────────


def test_conversation_thinking_event_type_value() -> None:
    assert StreamEventType.CONVERSATION_THINKING == "conversation_thinking"


def test_conversation_action_classified_event_type_value() -> None:
    assert StreamEventType.CONVERSATION_ACTION_CLASSIFIED == "conversation_action_classified"


def test_conversation_message_event_type_value() -> None:
    assert StreamEventType.CONVERSATION_MESSAGE == "conversation_message"


def test_event_types_are_strings() -> None:
    """StrEnum values must be usable as plain strings in SSE dicts."""
    evt = {"type": StreamEventType.CONVERSATION_THINKING}
    assert evt["type"] == "conversation_thinking"
    assert json.dumps(evt) == '{"type": "conversation_thinking"}'


# ── conversation_thinking payload ─────────────────────────────────────────────


def test_conversation_thinking_payload_is_empty_dict() -> None:
    payload = {"type": StreamEventType.CONVERSATION_THINKING}
    serialized = json.loads(json.dumps(payload))
    assert serialized == {"type": "conversation_thinking"}


# ── conversation_action_classified payload ────────────────────────────────────


def test_conversation_action_classified_refine_payload() -> None:
    payload = {
        "type": StreamEventType.CONVERSATION_ACTION_CLASSIFIED,
        "action": "refine",
        "args_summary": "Direct flights only, under ₹25,000",
        "args": {"direct_only": True, "price_max_inr": 25000, "sort_by": "price"},
    }
    serialized = json.loads(json.dumps(payload))
    assert serialized["type"] == "conversation_action_classified"
    assert serialized["action"] == "refine"
    assert serialized["args_summary"] == "Direct flights only, under ₹25,000"
    assert serialized["args"]["direct_only"] is True


def test_conversation_action_classified_replan_payload() -> None:
    payload = {
        "type": StreamEventType.CONVERSATION_ACTION_CLASSIFIED,
        "action": "replan",
        "args_summary": "Searching Mumbai to Bangkok in December",
        "args": {"destination_iata": "BKK"},
    }
    serialized = json.loads(json.dumps(payload))
    assert serialized["action"] == "replan"
    assert serialized["args"]["destination_iata"] == "BKK"


def test_conversation_action_classified_no_op_payload() -> None:
    payload = {
        "type": StreamEventType.CONVERSATION_ACTION_CLASSIFIED,
        "action": "no_op",
        "args_summary": "",
        "args": {"explanation": "I help with flight searches."},
    }
    serialized = json.loads(json.dumps(payload))
    assert serialized["action"] == "no_op"
    assert serialized["args_summary"] == ""


# ── conversation_message payload ──────────────────────────────────────────────


def test_conversation_message_no_op_payload() -> None:
    payload = {
        "type": StreamEventType.CONVERSATION_MESSAGE,
        "text": "I help refine flight searches. Want to filter options?",
    }
    serialized = json.loads(json.dumps(payload))
    assert serialized["type"] == "conversation_message"
    assert serialized["text"] == "I help refine flight searches. Want to filter options?"


def test_conversation_message_empty_pool_payload() -> None:
    payload = {
        "type": StreamEventType.CONVERSATION_MESSAGE,
        "text": (
            "No flights match those filters. Want to try different criteria or start a new search?"
        ),
    }
    serialized = json.loads(json.dumps(payload))
    assert "No flights match" in serialized["text"]


# ── all three new event types are distinct ────────────────────────────────────


def test_three_conversation_event_types_are_distinct() -> None:
    types = {
        StreamEventType.CONVERSATION_THINKING,
        StreamEventType.CONVERSATION_ACTION_CLASSIFIED,
        StreamEventType.CONVERSATION_MESSAGE,
    }
    assert len(types) == 3
