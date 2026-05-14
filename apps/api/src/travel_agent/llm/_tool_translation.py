"""Format translation between ToolDefinition/ToolCall and provider wire shapes.

Two distinct shapes exist in the wild:
  Anthropic      content blocks  type="tool_use":  {id, name, input}
  OpenAI-compat  message.tool_calls:                {id, function: {name, arguments}}

Neither format leaks into application code — all adapters call these helpers and
return the unified ToolCall dataclass.
"""
from __future__ import annotations

import json
from typing import Any, cast

from travel_agent.llm.base import ToolCall, ToolDefinition


def to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert ToolDefinition list to Anthropic Messages API tool format."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def to_openai_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert ToolDefinition list to OpenAI function-calling tool format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def parse_anthropic_tool_calls(content_blocks: list[Any]) -> list[ToolCall]:
    """Extract ToolCalls from an Anthropic content-block list."""
    return [
        ToolCall(name=block.name, input=cast(dict[str, Any], block.input), id=block.id)
        for block in content_blocks
        if getattr(block, "type", None) == "tool_use"
    ]


def parse_openai_tool_calls(tool_calls: list[Any] | None) -> list[ToolCall]:
    """Extract ToolCalls from an OpenAI-compatible message.tool_calls list."""
    if not tool_calls:
        return []
    return [
        ToolCall(
            name=tc.function.name,
            input=cast(dict[str, Any], json.loads(tc.function.arguments)),
            id=tc.id or "",
        )
        for tc in tool_calls
    ]
