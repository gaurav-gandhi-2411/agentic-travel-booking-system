"""Wave 2 eval runner — generates and caches planner, refine, and optimizer outputs.

Calls PlannerAgent, ConversationManagerAgent, and OptimizerAgent against the
Wave 2 golden dataset. Outputs are cached to evals/wave2/runs/ so the Tier-1
scorer and Tier-2 judge can re-run without spending Groq tokens.

Usage:
    # Generate all cases with the demo-llama profile
    python -m evals.wave2.runner

    # Generate with alternate profile (separate Groq bucket from Llama)
    python -m evals.wave2.runner --profile demo-gpt-oss-120b

    # Frugal: first N cases only, planner + refine only (skip optimizer)
    python -m evals.wave2.runner --limit 10 --no-optimizer

Output: evals/wave2/runs/<ISO-timestamp>_<profile>.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from travel_agent.agents.conversation_manager import ConversationManagerAgent
from travel_agent.agents.optimizer import OptimizerAgent
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

# Synthetic flight pool for ConversationManagerAgent and OptimizerAgent context.
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


@dataclass
class _Agents:
    planner: PlannerAgent
    conv: ConversationManagerAgent
    opt: OptimizerAgent


def _load_golden() -> list[dict[str, Any]]:
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def _resolve_agents(profile: str) -> _Agents:
    """Instantiate the three agents for the given routing profile."""
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
        # Flat profile — same client + model for every agent
        client = get_llm_client_for_provider(cfg["provider"])
        model: str = cfg["model"]
        return _Agents(
            planner=PlannerAgent(client=client, model=model),
            conv=ConversationManagerAgent(client=client, model=model, extra_params=extra_params),
            opt=OptimizerAgent(client=client, model=model),
        )

    planner_client, planner_model = get_llm_client_and_model("planner", profile)
    conv_client, conv_model = get_llm_client_and_model("conversation", profile)
    opt_client, opt_model = get_llm_client_and_model("optimizer", profile)
    return _Agents(
        planner=PlannerAgent(client=planner_client, model=planner_model),
        conv=ConversationManagerAgent(client=conv_client, model=conv_model),
        opt=OptimizerAgent(client=opt_client, model=opt_model),
    )


async def _generate_one(
    case: dict[str, Any],
    agents: _Agents,
    profile: str,
    *,
    run_optimizer: bool,
) -> dict[str, Any]:
    planner_model: str = agents.planner._model  # type: ignore[attr-defined]
    conv_model: str = agents.conv._model  # type: ignore[attr-defined]
    opt_model: str = agents.opt._model  # type: ignore[attr-defined]
    record: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "today": case["today"],
        "profile": profile,
        "model_planner": planner_model,
        "model_conversation": conv_model if case.get("refine") else None,
        "model_optimizer": opt_model if run_optimizer else None,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "latency_ms_planner": None,
        "latency_ms_conversation": None,
        "latency_ms_optimizer": None,
        "intent": None,
        "intent_error": None,
        "refine_classified": None,
        "refine_error": None,
        "optimizer_archetypes": None,
        "optimizer_error": None,
    }

    # --- Planner ---
    t0 = time.monotonic()
    try:
        state = RequestState(raw_input=case["query"])
        result = await agents.planner.run(state, today=date.fromisoformat(case["today"]))
        record["latency_ms_planner"] = round((time.monotonic() - t0) * 1000, 1)
        if result.intent is None:
            record["intent_error"] = "planner returned None intent"
        else:
            record["intent"] = result.intent.model_dump(mode="json")
    except Exception as exc:
        record["latency_ms_planner"] = round((time.monotonic() - t0) * 1000, 1)
        record["intent_error"] = str(exc)
        _safe = str(exc).encode("ascii", "replace").decode()
        _logger.warning("planner_error", case_id=case["id"], error=_safe)

    # --- ConversationManagerAgent (refine cases only) ---
    if case.get("refine") and record["intent"] is not None:
        intent = TravelIntent.model_validate(record["intent"])
        refine_state = RequestState(
            raw_input=case["refine"]["message"],
            intent=intent,
            flight_options=SYNTHETIC_REFINE_POOL,
        )
        t0 = time.monotonic()
        try:
            classified = await agents.conv.understand(case["refine"]["message"], refine_state)
            record["latency_ms_conversation"] = round((time.monotonic() - t0) * 1000, 1)
            record["refine_classified"] = classified.model_dump(mode="json")
        except Exception as exc:
            record["latency_ms_conversation"] = round((time.monotonic() - t0) * 1000, 1)
            record["refine_error"] = str(exc)
            _safe = str(exc).encode("ascii", "replace").decode()
            _logger.warning("refine_error", case_id=case["id"], error=_safe)

    # --- OptimizerAgent (Tier-2 input; skipped if --no-optimizer or planner failed) ---
    if run_optimizer and record["intent"] is not None:
        intent = TravelIntent.model_validate(record["intent"])
        opt_state = RequestState(
            raw_input=case["query"],
            intent=intent,
            flight_options=SYNTHETIC_REFINE_POOL,
        )
        t0 = time.monotonic()
        try:
            opt_result = await agents.opt.run(opt_state, today=date.fromisoformat(case["today"]))
            record["latency_ms_optimizer"] = round((time.monotonic() - t0) * 1000, 1)
            record["optimizer_archetypes"] = [
                a.model_dump(mode="json") for a in opt_result.archetypes
            ]
        except Exception as exc:
            record["latency_ms_optimizer"] = round((time.monotonic() - t0) * 1000, 1)
            record["optimizer_error"] = str(exc)
            # Sanitize for terminals that can't encode full Unicode (e.g. Windows cp1252)
            safe_err = str(exc).encode("ascii", "replace").decode()
            _logger.warning("optimizer_error", case_id=case["id"], error=safe_err)

    return record


async def generate_all(
    cases: list[dict[str, Any]],
    profile: str,
    limit: int | None,
    *,
    run_optimizer: bool,
) -> list[dict[str, Any]]:
    agents = _resolve_agents(profile)
    planner_model: str = agents.planner._model  # type: ignore[attr-defined]

    if limit is not None:
        cases = cases[:limit]

    opt_tag = " + optimizer" if run_optimizer else " (no optimizer)"
    print(f"Generating {len(cases)} cases — profile={profile}, planner={planner_model}{opt_tag}")

    records: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        print(f"  [{i:>2}/{len(cases)}] {case['id']}  {case['query'][:55]}")
        record = await _generate_one(case, agents, profile, run_optimizer=run_optimizer)
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

        if run_optimizer and record["intent"] is not None:
            n_arch = len(record["optimizer_archetypes"]) if record["optimizer_archetypes"] else 0
            o_status = (
                f"{record['latency_ms_optimizer']}ms OK ({n_arch} archetypes)"
                if record["optimizer_archetypes"] is not None
                else f"ERR: {(record.get('optimizer_error') or '?')[:60]}"
            )
            print(f"         optimizer: {o_status}")

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
    parser = argparse.ArgumentParser(description="Wave 2 eval runner — generate outputs")
    parser.add_argument(
        "--profile", default=_DEFAULT_PROFILE,
        help=f"LLM routing profile (default: {_DEFAULT_PROFILE})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Generate only first N cases (frugal mode)",
    )
    parser.add_argument(
        "--no-optimizer", action="store_true",
        help="Skip optimizer call (faster; Tier-1 only run)",
    )
    args = parser.parse_args()

    cases = _load_golden()
    print(f"Loaded {len(cases)} cases from {_GOLDEN_FILE.name}")

    records = await generate_all(
        cases, args.profile, args.limit, run_optimizer=not args.no_optimizer
    )
    save_run(args.profile, records)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
