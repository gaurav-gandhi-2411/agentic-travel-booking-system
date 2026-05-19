"""Conversation manager eval runner.

Usage:
    python -m evals.conversation_manager.runner                    # default profiles
    python -m evals.conversation_manager.runner --profile demo-llama
    python -m evals.conversation_manager.runner --all-profiles     # all active profiles
    python -m evals.conversation_manager.runner --dry-run          # no LLM calls

Default profiles: demo-llama, demo-gpt-oss-120b (both free tier).
demo-haiku is opt-in via --profile demo-haiku.

Output: evals/conversation_manager/runs/<ISO-timestamp>_<profile>.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))  # exposes optimizer.throttle

import structlog
from optimizer.throttle import (
    RPM_LIMITS,
    TPM_LIMITS,
    RequestTracker,
    ThrottledLLMClient,
    TokenTracker,
)

from travel_agent.agents.conversation_manager import ConversationManagerAgent
from travel_agent.coordinator.state import (
    Archetype,
    ArchetypeLabel,
    CabinClass,
    FlightOption,
    RequestState,
    TravelIntent,
    Window,
)

_SCENARIOS_FILE = Path(__file__).parent / "scenarios.json"
_RUNS_DIR = Path(__file__).parent / "runs"

# Free-tier defaults. demo-haiku opt-in (paid); demo-deepseek-v4 excluded (finite NIM credits).
_PROFILES = ["demo-llama", "demo-gpt-oss-120b"]

_logger = structlog.get_logger(__name__)


def _load_scenarios() -> list[dict]:
    return json.loads(_SCENARIOS_FILE.read_text(encoding="utf-8"))


def _resolve_client_and_model(profile_cfg: dict, profile: str) -> tuple[object, str]:
    """Return (client, model) for flat or agent-routed profiles."""
    from travel_agent.llm import (  # noqa: PLC0415
        get_llm_client_and_model,
        get_llm_client_for_provider,
    )

    provider = profile_cfg.get("provider", "")
    if "model" in profile_cfg:
        return get_llm_client_for_provider(provider), profile_cfg["model"]
    return get_llm_client_and_model("conversation", profile)


def _build_state(scenario: dict) -> RequestState:
    """Build a RequestState from a scenario's prior search context."""
    intent_d = scenario["prior_intent"]
    summary = scenario["prior_flights_summary"]
    arch_spec = scenario["prior_archetypes"]

    intent = TravelIntent(
        origin_iata=intent_d["origin_iata"],
        destination_iata=intent_d["destination_iata"],
        earliest_departure=date.fromisoformat(intent_d["departure_window_start"]),
        latest_departure=date.fromisoformat(intent_d["departure_window_end"]),
        budget_inr=intent_d.get("budget_max_inr"),
    )

    dep_start = date.fromisoformat(intent_d["departure_window_start"])
    window = Window(start_date=dep_start, end_date=dep_start + timedelta(days=6))
    origin, dest = intent_d["origin_iata"], intent_d["destination_iata"]
    price_min, price_max = summary["price_min"], summary["price_max"]
    stops_min, stops_max = summary["stops_min"], summary["stops_max"]

    # Archetype flights created first to preserve exact price/stops from scenario spec.
    arch_flights = [
        FlightOption(
            window=window,
            provider="synthetic",
            origin_iata=origin,
            destination_iata=dest,
            outbound_departure_at=f"{dep_start}T08:00:00",
            outbound_arrival_at=f"{dep_start}T14:00:00",
            airline_code="XX",
            flight_number=f"XX-A{i:02d}",
            cabin_class=CabinClass.ECONOMY,
            price_inr=a["price_inr"],
            outbound_duration_minutes=360,
            layover_count=a["stops"],
            is_refundable=False,
        )
        for i, a in enumerate(arch_spec)
    ]

    # Fill remaining slots to match scenario count, spanning full price/stops range.
    n_fill = max(0, summary["count"] - len(arch_spec))
    fill_flights = []
    for i in range(n_fill):
        frac = i / max(n_fill - 1, 1)
        price = int(price_min + frac * (price_max - price_min))
        stops = (
            stops_min + (i % (stops_max - stops_min + 1))
            if stops_max > stops_min
            else stops_min
        )
        fill_flights.append(
            FlightOption(
                window=window,
                provider="synthetic",
                origin_iata=origin,
                destination_iata=dest,
                outbound_departure_at=f"{dep_start}T08:00:00",
                outbound_arrival_at=f"{dep_start}T14:00:00",
                airline_code="XX",
                flight_number=f"XX-F{i:03d}",
                cabin_class=CabinClass.ECONOMY,
                price_inr=price,
                outbound_duration_minutes=360,
                layover_count=stops,
                is_refundable=False,
            )
        )

    label_map = {
        "best-value": ArchetypeLabel.BEST_VALUE,
        "best-experience": ArchetypeLabel.BEST_EXPERIENCE,
    }
    archetypes = [
        Archetype(
            label=label_map[a["label"]],
            flight=arch_flights[i],
            explanation="Synthetic archetype for eval",
            deeplink_url="https://example.com",
        )
        for i, a in enumerate(arch_spec)
    ]

    return RequestState(
        raw_input=scenario["user_message"],
        intent=intent,
        flight_options=arch_flights + fill_flights,
        archetypes=archetypes,
    )


async def run_profile(
    profile: str,
    scenarios: list[dict],
    dry_run: bool,
) -> list[dict]:
    """Run all scenarios under one profile. Returns list of result records."""
    if dry_run:
        return [
            {
                "id": s["id"],
                "category": s["category"],
                "user_message": s["user_message"],
                "expected_action": s["expected_action"],
                "expected_args": s["expected_args"],
                "profile": profile,
                "model": "dry-run",
                "actual_output": None,
                "latency_ms": None,
                "dry_run": True,
            }
            for s in scenarios
        ]

    try:
        from travel_agent.llm.routing import load_routing_config  # noqa: PLC0415

        routing = load_routing_config()
        if profile not in routing:
            _logger.warning(
                "eval_profile_skipped",
                profile=profile,
                reason="profile not in llm_routing.yaml (demoted or commented out)",
            )
            return []

        profile_cfg = routing[profile]
        provider = profile_cfg.get("provider", "")
        client, model = _resolve_client_and_model(profile_cfg, profile)
        extra_params: dict | None = profile_cfg.get("extra_params") or None

        if provider in TPM_LIMITS or provider in RPM_LIMITS:
            tpm_tracker = TokenTracker(TPM_LIMITS[provider]) if provider in TPM_LIMITS else None
            rpm_tracker = RequestTracker(RPM_LIMITS[provider]) if provider in RPM_LIMITS else None
            client = ThrottledLLMClient(client, tpm_tracker, rpm_tracker=rpm_tracker)

    except Exception as exc:
        _logger.warning("eval_profile_skipped", profile=profile, reason=str(exc))
        return []

    agent = ConversationManagerAgent(client=client, model=model, extra_params=extra_params)
    results: list[dict] = []

    for scenario in scenarios:
        state = _build_state(scenario)
        t0 = time.monotonic()
        try:
            output = await agent.understand(scenario["user_message"], state)
            latency_ms = (time.monotonic() - t0) * 1000
            results.append(
                {
                    "id": scenario["id"],
                    "category": scenario["category"],
                    "user_message": scenario["user_message"],
                    "expected_action": scenario["expected_action"],
                    "expected_args": scenario["expected_args"],
                    "profile": profile,
                    "model": model,
                    "actual_output": output.model_dump(mode="json"),
                    "latency_ms": round(latency_ms, 1),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": scenario["id"],
                    "category": scenario["category"],
                    "user_message": scenario["user_message"],
                    "expected_action": scenario["expected_action"],
                    "expected_args": scenario["expected_args"],
                    "profile": profile,
                    "model": model,
                    "actual_output": None,
                    "latency_ms": None,
                    "error": str(exc),
                }
            )

    return results


def save_run(profile: str, records: list[dict]) -> Path:
    _RUNS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    out = _RUNS_DIR / f"{ts}_{profile}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print(f"[{profile}] Saved {len(records)} records -> {out}")
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="Conversation manager eval runner")
    parser.add_argument("--profile", action="append", dest="profiles", metavar="PROFILE")
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()

    profiles = _PROFILES if args.all_profiles else (args.profiles or _PROFILES)
    scenarios = _load_scenarios()
    print(f"Loaded {len(scenarios)} scenarios")

    for profile in profiles:
        records = await run_profile(profile, scenarios, dry_run=args.dry_run)
        if records:
            save_run(profile, records)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
