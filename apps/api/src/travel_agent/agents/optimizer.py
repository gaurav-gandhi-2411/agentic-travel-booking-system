"""OptimizerAgent — Pareto frontier + 2 archetype packages.

Phase C (demo): produces two Archetype objects (best-value, best-experience)
from the Pareto frontier of flight options.  Uses claude-sonnet-4-6 to generate
a short NL explanation for each archetype (2 LLM calls per search).

Phase D will extend this to flight+hotel package scoring and full HITL booking.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from travel_agent.agents.tools import GENERATE_ARCHETYPE_EXPLANATION
from travel_agent.coordinator.state import (
    Archetype,
    ArchetypeLabel,
    FlightOption,
    RequestState,
)
from travel_agent.llm.base import LLMClient, Message
from travel_agent.providers.aviasales.deeplink import build_deeplink
from travel_agent.utility.experience import experience_score
from travel_agent.utility.pareto import pareto_frontier
from travel_agent.utility.value import value_score

_PROMPT_PATH = Path(__file__).parent / "prompts" / "optimizer_system.txt"


def _load_system_prompt(today: date | None = None) -> str:
    template = _PROMPT_PATH.read_text()
    resolved_today = (today or datetime.now(tz=UTC).date()).isoformat()
    return template.replace("{today}", resolved_today)


class OptimizerAgent:
    def __init__(
        self,
        client: LLMClient | None = None,
        model: str = "claude-sonnet-4-6",
        partner_marker: str = "",
    ) -> None:
        self._client = client
        self._model = model
        self._partner_marker = partner_marker

    async def run(
        self,
        state: RequestState,
        *,
        today: date | None = None,
    ) -> RequestState:
        if not state.flight_options:
            return state

        # Pareto frontier
        frontier = pareto_frontier(
            state.flight_options,
            value_score,
            experience_score,
        )
        if not frontier:
            frontier = state.flight_options  # fallback: use all options

        # Pick archetypes: best on each axis
        best_value = max(frontier, key=value_score)
        best_exp = max(frontier, key=experience_score)

        system = _load_system_prompt(today)

        archetypes: list[Archetype] = []
        for label, flight in [
            (ArchetypeLabel.BEST_VALUE, best_value),
            (ArchetypeLabel.BEST_EXPERIENCE, best_exp),
        ]:
            explanation = await self._explain(flight, label, system)
            deeplink = self._build_deeplink(flight, label)
            breakdown = _score_breakdown(flight)
            archetypes.append(
                Archetype(
                    label=label,
                    flight=flight,
                    explanation=explanation,
                    deeplink_url=deeplink,
                    score_breakdown=breakdown,
                )
            )

        state.archetypes = archetypes
        return state

    async def _explain(
        self,
        flight: FlightOption,
        label: ArchetypeLabel,
        system: str,
    ) -> str:
        if self._client is None:
            return _fallback_explanation(flight, label)

        summary = _flight_summary(flight, label)
        messages = [Message(role="user", content=summary)]
        response = await self._client.chat(
            messages,
            model=self._model,
            max_tokens=256,
            temperature=0.3,
            system=system,
            tools=[GENERATE_ARCHETYPE_EXPLANATION],
        )
        if response.tool_calls:
            raw = response.tool_calls[0].input
            return str(raw.get("explanation", "")).strip() or _fallback_explanation(flight, label)
        return _fallback_explanation(flight, label)

    def _build_deeplink(self, flight: FlightOption, label: ArchetypeLabel) -> str:
        if not self._partner_marker:
            return ""
        return build_deeplink(
            raw_link=str(flight.raw.get("link", "")) or None,
            origin_iata=flight.origin_iata,
            destination_iata=flight.destination_iata,
            departure_date=flight.outbound_departure_at[:10],
            partner_marker=self._partner_marker,
            archetype_label=str(label),
        )


def _flight_summary(flight: FlightOption, label: ArchetypeLabel) -> str:
    stops = "non-stop" if flight.layover_count == 0 else f"{flight.layover_count} stop(s)"
    hrs = flight.outbound_duration_minutes // 60
    mins = flight.outbound_duration_minutes % 60
    return (
        f"Archetype: {label}\n"
        f"Route: {flight.origin_iata} -> {flight.destination_iata}\n"
        f"Airline: {flight.airline_code}  Flight: {flight.flight_number}\n"
        f"Price: INR {flight.price_inr:,}\n"
        f"Duration: {hrs}h {mins}m  |  {stops}\n"
        f"Departs: {flight.outbound_departure_at}\n"
        f"Cabin: {flight.cabin_class}"
    )


def _fallback_explanation(flight: FlightOption, label: ArchetypeLabel) -> str:
    if label == ArchetypeLabel.BEST_VALUE:
        return (
            f"Lowest price at INR {flight.price_inr:,} with {flight.layover_count} stop(s). "
            "Best choice if budget is the priority."
        )
    hrs = flight.outbound_duration_minutes // 60
    stops = "non-stop" if flight.layover_count == 0 else f"{flight.layover_count} stop(s)"
    return f"Fastest option at {hrs}h total, {stops}. Best choice for comfort and convenience."


def _score_breakdown(flight: FlightOption) -> dict[str, float]:
    return {
        "value_score": round(value_score(flight), 3),
        "experience_score": round(experience_score(flight), 3),
    }
