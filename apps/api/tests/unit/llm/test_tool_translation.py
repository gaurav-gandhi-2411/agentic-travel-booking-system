"""Unit tests for _tool_translation helpers — no HTTP, no SDK, pure data transformation."""
import json

import pytest

from travel_agent.llm._tool_translation import (
    parse_anthropic_tool_calls,
    parse_openai_tool_calls,
    to_anthropic_tools,
    to_openai_tools,
)
from travel_agent.llm.base import ToolDefinition

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def flight_tool() -> ToolDefinition:
    return ToolDefinition(
        name="search_flights",
        description="Search for available flights",
        input_schema={
            "type": "object",
            "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}},
            "required": ["origin", "destination"],
        },
    )


# ── to_anthropic_tools ────────────────────────────────────────────────────────


def test_to_anthropic_tools_structure(flight_tool: ToolDefinition) -> None:
    result = to_anthropic_tools([flight_tool])
    assert len(result) == 1
    tool = result[0]
    assert tool["name"] == "search_flights"
    assert tool["description"] == "Search for available flights"
    assert tool["input_schema"] == flight_tool.input_schema


def test_to_anthropic_tools_empty() -> None:
    assert to_anthropic_tools([]) == []


def test_to_anthropic_tools_multiple(flight_tool: ToolDefinition) -> None:
    hotel_tool = ToolDefinition(name="search_hotels", description="Hotels", input_schema={})
    result = to_anthropic_tools([flight_tool, hotel_tool])
    assert [t["name"] for t in result] == ["search_flights", "search_hotels"]


# ── to_openai_tools ───────────────────────────────────────────────────────────


def test_to_openai_tools_structure(flight_tool: ToolDefinition) -> None:
    result = to_openai_tools([flight_tool])
    assert len(result) == 1
    tool = result[0]
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "search_flights"
    assert fn["description"] == "Search for available flights"
    assert fn["parameters"] == flight_tool.input_schema


def test_to_openai_tools_uses_parameters_not_input_schema(flight_tool: ToolDefinition) -> None:
    result = to_openai_tools([flight_tool])
    fn = result[0]["function"]
    assert "parameters" in fn
    assert "input_schema" not in fn


def test_to_openai_tools_empty() -> None:
    assert to_openai_tools([]) == []


# ── parse_anthropic_tool_calls ────────────────────────────────────────────────


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, name: str, input: dict, id: str) -> None:
        self.name = name
        self.input = input
        self.id = id


class _FakeTextBlock:
    type = "text"
    text = "Hello!"


def test_parse_anthropic_single_tool_call() -> None:
    block = _FakeToolUseBlock("search_flights", {"origin": "BOM", "destination": "CDG"}, "toolu_01")
    result = parse_anthropic_tool_calls([block])
    assert len(result) == 1
    tc = result[0]
    assert tc.name == "search_flights"
    assert tc.input == {"origin": "BOM", "destination": "CDG"}
    assert tc.id == "toolu_01"


def test_parse_anthropic_skips_text_blocks() -> None:
    blocks: list[object] = [_FakeTextBlock(), _FakeToolUseBlock("fn", {}, "id1")]
    result = parse_anthropic_tool_calls(blocks)
    assert len(result) == 1
    assert result[0].name == "fn"


def test_parse_anthropic_empty_list() -> None:
    assert parse_anthropic_tool_calls([]) == []


def test_parse_anthropic_multiple_tool_calls() -> None:
    blocks: list[object] = [
        _FakeToolUseBlock("search_flights", {"origin": "BOM"}, "id1"),
        _FakeToolUseBlock("search_hotels", {"city": "Paris"}, "id2"),
    ]
    result = parse_anthropic_tool_calls(blocks)
    assert [tc.name for tc in result] == ["search_flights", "search_hotels"]


# ── parse_openai_tool_calls ───────────────────────────────────────────────────


class _FakeOAIFunction:
    def __init__(self, name: str, args: dict) -> None:
        self.name = name
        self.arguments = json.dumps(args)


class _FakeOAIToolCall:
    def __init__(self, name: str, args: dict, call_id: str) -> None:
        self.function = _FakeOAIFunction(name, args)
        self.id = call_id


def test_parse_openai_single_tool_call() -> None:
    tc_obj = _FakeOAIToolCall("search_hotels", {"city": "Paris", "stars": 4}, "call_abc")
    result = parse_openai_tool_calls([tc_obj])
    assert len(result) == 1
    tc = result[0]
    assert tc.name == "search_hotels"
    assert tc.input == {"city": "Paris", "stars": 4}
    assert tc.id == "call_abc"


def test_parse_openai_none_returns_empty() -> None:
    assert parse_openai_tool_calls(None) == []


def test_parse_openai_empty_list() -> None:
    assert parse_openai_tool_calls([]) == []


def test_parse_openai_multiple_tool_calls() -> None:
    calls: list[object] = [
        _FakeOAIToolCall("fn_a", {"x": 1}, "id1"),
        _FakeOAIToolCall("fn_b", {"y": 2}, "id2"),
    ]
    result = parse_openai_tool_calls(calls)
    assert [tc.name for tc in result] == ["fn_a", "fn_b"]
    assert result[0].input == {"x": 1}
    assert result[1].input == {"y": 2}


def test_parse_openai_empty_call_id_falls_back_to_empty_string() -> None:
    class NoId:
        function = _FakeOAIFunction("fn", {})
        id = ""

    result = parse_openai_tool_calls([NoId()])
    assert result[0].id == ""
