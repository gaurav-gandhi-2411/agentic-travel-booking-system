"""Wave 2 Tier-1 eval runner — generates agent outputs and caches to JSONL.

Calls PlannerAgent (and ConversationManagerAgent for refine cases) against the
Wave 2 golden dataset. Outputs are cached to evals/wave2/runs/ so the scorer can
re-run without spending Groq tokens.

Usage:
    # Generate all cases with the demo-llama profile
    python -m evals.wave2.runner

    # Generate with alternate profile (separate Groq bucket from Llama)
    python -m evals.wave2.runner --profile demo-gpt-oss-120b

    # Frugal: first N cases only
    python -m evals.wave2.runner --limit 10

Output: evals/wave2/runs/<ISO-timestamp>_<profile>.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from travel_agent.agents.conversation_manager import ConversationManagerAgent
from travel_agent.agents.planner import PlannerAgent
from travel_agent.coordinator.state import (
    CabinClass,
    FlightOption,
    RequestState,
    TravelIntent,
    Window,
)

_logger = structlog.get_logger(__name__)

_GOLDEN_FILE = Path(__file__).parent / "golden.json"
_RUNS_DIR = Path(__file__).parent / "runs"

# Default free-tier profiles for Wave 2 generation.
# demo-llama is the production planner profile (Groq Llama 3.3 70B).
# demo-gpt-oss-120b uses a separate Groq token bucket — use as TPD fallback.
_DEFAULT_PROFILE = "demo-llama"

# Synthetic flight pool for ConversationManagerAgent context in refine cases.
# Route/dates are arbitrary — only stop counts, prices, and departure hours matter
# for the three constraint types: direct_only, price_sort, morning_departure.
_POOL_WINDOW = Window(start_date=date(2026, 9, 15), end_date=date(2026, 9, 21))
SYNTHETIC_REFINE_POOL: list[FlightOption] = [
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T07:30:00",
        outbound_arrival_at="2026-09-16T03:30:00",
        airline_code="AI", flight_number="AI-301",
        cabin_class=CabinClass.ECONOMY, price_inr=35000,
        outbound_duration_minutes=480, layover_count=0,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T14:00:00",
        outbound_arrival_at="2026-09-16T12:00:00",
        airline_code="EK", flight_number="EK-502",
        cabin_class=CabinClass.ECONOMY, price_inr=28000,
        outbound_duration_minutes=540, layover_count=1,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T22:00:00",
        outbound_arrival_at="2026-09-16T22:00:00",
        airline_code="QR", flight_number="QR-147",
        cabin_class=CabinClass.ECONOMY, price_inr=22000,
        outbound_duration_minutes=720, layover_count=2,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T18:00:00",
        outbound_arrival_at="2026-09-16T14:00:00",
        airline_code="SQ", flight_number="SQ-401",
        cabin_class=CabinClass.ECONOMY, price_inr=48000,
        outbound_duration_minutes=420, layover_count=0,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T09:00:00",
        outbound_arrival_at="2026-09-16T07:00:00",
        airline_code="9W", flight_number="9W-802",
        cabin_class=CabinClass.ECONOMY, price_inr=31000,
        outbound_duration_minutes=600, layover_count=1,
    ),
    FlightOption(
        window=_POOL_WINDOW, provider="synthetic",
        origin_iata="BOM", destination_iata="NRT",
        outbound_departure_at="2026-09-15T23:30:00",
        outbound_arrival_at="2026-09-16T23:00:00",
        airline_code="TK", flight_number="TK-221",
        cabin_class=CabinClass.ECONOMY, price_inr=52000,
        outbound_duration_minutes=390, layover_count=0,
    ),
]


def _load_golden() -> list[dict[str, Any]]:
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def _resolve_clients(
    profile: str,
) -> tuple[Any, str, Any, str, dict[str, Any] | None]:
    """Return (planner_client, planner_model, conv_client, conv_model, extra_params)."""
    from travel_agent.llm import (  # noqa: PLC0415
        get_llm_client_and_model,
        get_llm_client_for_provider,
    )
    from travel_agent.llm.routing import load_routing_config  # noqa: PLC0415

    routing = load_routing_config()
    cfg = routing.get(profile, {})
    if not cfg:
        msg = f"Profile {profile!r} not found in llm_routing.yaml"
        raise ValueError(msg)

    extra_params: dict[str, Any] | None = cfg.get("extra_params") or None

    if "model" in cfg:
        # Flat profile — same model for every agent
        client = get_llm_client_for_provider(cfg["provider"])
        model = cfg["model"]
        return client, model, client, model, extra_params

    planner_client, planner_model = get_llm_client_and_model("planner", profile)
    conv_client, conv_model = get_llm_client_and_model("conversation", profile)
    return planner_client, planner_model, conv_client, conv_model, extra_params


async def _generate_one(
    case: dict[str, Any],
    planner: PlannerAgent,
    conv_agent: ConversationManagerAgent,
    profile: str,
) -> dict[str, Any]:
    planner_model: str = planner._model  # type: ignore[attr-defined]
    conv_model: str = conv_agent._model  # type: ignore[attr-defined]
    record: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "today": case["today"],
        "profile": profile,
        "model_planner": planner_model,
        "model_conversation": conv_model if case.get("refine") else None,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "latency_ms_planner": None,
        "latency_ms_conversation": None,
        "intent": None,
        "intent_error": None,
        "refine_classified": None,
        "refine_error": None,
    }

    # Planner call
    t0 = time.monotonic()
    try:
        state = RequestState(raw_input=case["query"])
        result = await planner.run(state, today=date.fromisoformat(case["today"]))
        record["latency_ms_planner"] = round((time.monotonic() - t0) * 1000, 1)
        if result.intent is None:
            record["intent_error"] = "planner returned None intent"
        else:
            record["intent"] = result.intent.model_dump(mode="json")
    except Exception as exc:
        record["latency_ms_planner"] = round((time.monotonic() - t0) * 1000, 1)
        record["intent_error"] = str(exc)
        _logger.warning("planner_error", case_id=case["id"], error=str(exc))

    # ConversationManagerAgent call (refine cases only, requires successful planner intent)
    if case.get("refine") and record["intent"] is not None:
        intent = TravelIntent.model_validate(record["intent"])
        refine_state = RequestState(
            raw_input=case["refine"]["message"],
            intent=intent,
            flight_options=SYNTHETIC_REFINE_POOL,
        )
        t0 = time.monotonic()
        try:
            classified = await conv_agent.understand(case["refine"]["message"], refine_state)
            record["latency_ms_conversation"] = round((time.monotonic() - t0) * 1000, 1)
            record["refine_classified"] = classified.model_dump(mode="json")
        except Exception as exc:
            record["latency_ms_conversation"] = round((time.monotonic() - t0) * 1000, 1)
            record["refine_error"] = str(exc)
            _logger.warning("refine_error", case_id=case["id"], error=str(exc))

    return record


async def generate_all(
    cases: list[dict[str, Any]],
    profile: str,
    limit: int | None,
) -> list[dict[str, Any]]:
    planner_client, planner_model, conv_client, conv_model, extra_params = _resolve_clients(profile)
    planner = PlannerAgent(client=planner_client, model=planner_model)
    conv_agent = ConversationManagerAgent(
        client=conv_client, model=conv_model, extra_params=extra_params
    )

    if limit is not None:
        cases = cases[:limit]

    print(f"Generating {len(cases)} cases with profile={profile} (planner: {planner_model})")

    records: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        print(f"  [{i:>2}/{len(cases)}] {case['id']}  {case['query'][:55]}")
        record = await _generate_one(case, planner, conv_agent, profile)
        records.append(record)

        p_status = (
            f"{record['latency_ms_planner']}ms OK"
            if record["intent"] is not None
            else f"ERR: {record.get('intent_error', '?')[:60]}"
        )
        print(f"         planner: {p_status}")

        if case.get("refine"):
            c_status = (
                f"{record['latency_ms_conversation']}ms OK"
                if record["refine_classified"] is not None
                else f"ERR: {(record.get('refine_error') or 'planner_failed')[:60]}"
            )
            print(f"         refine:  {c_status}")

    return records


def save_run(profile: str, records: list[dict[str, Any]]) -> Path:
    _RUNS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    out = _RUNS_DIR / f"{ts}_{profile}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    print(f"\nSaved {len(records)} records -> {out}")
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="Wave 2 Tier-1 eval runner — generate outputs")
    parser.add_argument(
        "--profile", default=_DEFAULT_PROFILE,
        help=f"LLM routing profile (default: {_DEFAULT_PROFILE})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Generate only first N cases (frugal mode)",
    )
    args = parser.parse_args()

    cases = _load_golden()
    print(f"Loaded {len(cases)} cases from {_GOLDEN_FILE.name}")

    records = await generate_all(cases, args.profile, args.limit)
    save_run(args.profile, records)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
