"""ConversationManagerAgent — Level 2 single-turn intent classification.

Classifies a user's refinement message into one of three actions:
  REFINE  — filter/sort the cached flight pool (no new provider calls)
  REPLAN  — trigger a new search with modified TravelIntent
  NO_OP   — off-topic input; return a polite redirect

Tool schema is derived from ConversationManagerOutput.model_json_schema()
so it stays in sync with the Pydantic models automatically.

See ADR-0019 for the Level 2 design rationale and eval gate.
"""

from __future__ import annotations

import contextlib
import copy
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from travel_agent.agents.conversation_manager_types import (
    ConversationAction,
    ConversationManagerOutput,
    NoOpArgs,
)
from travel_agent.coordinator.state import RequestState
from travel_agent.llm.base import LLMClient, Message, ToolDefinition
from travel_agent.observability.langfuse_client import get_langfuse, get_request_trace
from travel_agent.observability.pricing import compute_cost

_logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "conversation_manager_system.txt").read_text()

# Tool schema derived from the Pydantic output model — stays in sync automatically.
# The LLM must call this tool; free-text responses are treated as no_tool_call fallbacks.
#
# Post-processed to allow uppercase action values (e.g. "NO_OP"). Some Groq-hosted models
# (notably GPT-OSS-120B) return uppercase enum values; Groq validates tool call outputs
# against this schema before returning them, so the 400 would hit before our code runs.
# Expanding the enum to include uppercase variants lets Groq pass the response through;
# the field_validator in ConversationManagerOutput normalises to lowercase before coercion.
_OUTPUT_SCHEMA = copy.deepcopy(ConversationManagerOutput.model_json_schema())
if "ConversationAction" in _OUTPUT_SCHEMA.get("$defs", {}):
    _ca = _OUTPUT_SCHEMA["$defs"]["ConversationAction"]
    existing: list[str] = _ca.get("enum", [])
    _ca["enum"] = sorted({v.lower() for v in existing} | {v.upper() for v in existing})

EXTRACT_CONVERSATION_ACTION = ToolDefinition(
    name="extract_conversation_action",
    description=(
        "Classify the user's refinement message into REFINE, REPLAN, or NO_OP and "
        "extract structured arguments. Exactly one of refine_args, replan_args, or "
        "no_op_args must be populated to match the chosen action."
    ),
    input_schema=_OUTPUT_SCHEMA,
)

_FALLBACK_EXPLANATION = (
    "I help refine flight searches. Want to filter the current options or try a different route?"
)

_MAX_TOKENS = 512


def _build_context(state: RequestState) -> str:
    """Build a concise context string from the current search state."""
    parts: list[str] = []

    if state.intent:
        intent = state.intent
        parts.append(f"Route: {intent.origin_iata} → {intent.destination_iata}")
        parts.append(f"Dates: {intent.earliest_departure} - {intent.latest_departure}")
        if intent.budget_inr:
            parts.append(f"Budget: ₹{intent.budget_inr:,}")

    if state.flight_options:
        prices = [f.price_inr for f in state.flight_options]
        stops = [f.layover_count for f in state.flight_options]
        parts.append(
            f"Flight pool: {len(state.flight_options)} flights, "
            f"Rs.{min(prices):,}-Rs.{max(prices):,}, "
            f"{min(stops)}-{max(stops)} stops"
        )

    if state.archetypes:
        arch_lines = [
            f"  {a.label}: ₹{a.flight.price_inr:,}, {a.flight.layover_count} stop(s)"
            for a in state.archetypes
        ]
        parts.append("Current recommendations:\n" + "\n".join(arch_lines))

    return "\n".join(parts) if parts else "No search context available."


def _fallback_no_op() -> ConversationManagerOutput:
    return ConversationManagerOutput(
        action=ConversationAction.NO_OP,
        no_op_args=NoOpArgs(explanation=_FALLBACK_EXPLANATION),
    )


class ConversationManagerAgent:
    def __init__(
        self,
        client: LLMClient,
        model: str,
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._extra_params = extra_params or {}

    async def understand(
        self,
        message: str,
        state: RequestState,
    ) -> ConversationManagerOutput:
        context = _build_context(state)
        user_content = f"Current search context:\n{context}\n\nUser message: {message}"
        messages = [Message(role="user", content=user_content)]

        response = await self._client.chat(
            messages,
            model=self._model,
            max_tokens=_MAX_TOKENS,
            temperature=0.0,
            system=_SYSTEM_PROMPT,
            tools=[EXTRACT_CONVERSATION_ACTION],
            extra_params=self._extra_params or None,
        )

        cost = compute_cost(
            response.model,
            response.input_tokens,
            response.output_tokens,
            response.cache_read_input_tokens,
            response.cache_creation_input_tokens,
        )
        _logger.info(
            "llm_call",
            agent="conversation_manager",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=round(response.latency_ms, 1),
            cost_usd=cost,
        )

        # Classify the response and determine fallback_reason for telemetry
        fallback_reason = "none"
        output: ConversationManagerOutput

        if not response.tool_calls:
            fallback_reason = "no_tool_call"
            _logger.warning(
                "conversation_manager_no_tool_call",
                model=response.model,
                content_preview=response.content[:200],
            )
            output = _fallback_no_op()
        else:
            call = response.tool_calls[0]
            try:
                output = ConversationManagerOutput.model_validate(call.input)
                if output.action == ConversationAction.NO_OP:
                    _logger.info("conversation_manager_classified_no_op")
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                fallback_reason = "parse_failed"
                _logger.warning(
                    "conversation_manager_parse_failed",
                    model=response.model,
                    error=str(exc),
                    raw_input=str(call.input)[:500],
                )
                output = _fallback_no_op()

        # Langfuse span — optional, never breaks the agent
        with contextlib.suppress(Exception):
            trace = get_request_trace()
            if trace is not None:
                lf = get_langfuse()
                if lf is not None:
                    span_output: object = (
                        response.tool_calls[0].input if response.tool_calls else response.content
                    )
                    trace.start_observation(
                        name="conversation_manager_classify",
                        as_type="generation",
                        model=response.model,
                        input={"message": message, "context": context[:500]},
                        output=span_output,
                        usage_details={
                            "input": response.input_tokens,
                            "output": response.output_tokens,
                        },
                        metadata={
                            "latency_ms": round(response.latency_ms, 1),
                            "adapter": type(self._client).__name__,
                            "cost_usd": cost,
                            "fallback_reason": fallback_reason,
                            "action": output.action.value,
                        },
                    ).end()

        return output
