"""Optimizer eval runner.

Usage:
    python -m evals.optimizer.runner                    # dry run (no LLM calls)
    python -m evals.optimizer.runner --profile demo-haiku
    python -m evals.optimizer.runner --profile demo-haiku --profile demo-llama
    python -m evals.optimizer.runner --all-profiles     # all active profiles
    python -m evals.optimizer.runner --dry-run          # deterministic only, no LLM

Output: evals/optimizer/runs/<ISO-timestamp>_<profile>.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import structlog
from optimizer.throttle import TPM_LIMITS, ThrottledLLMClient, TokenTracker

from travel_agent.agents.optimizer import OptimizerAgent
from travel_agent.coordinator.state import FlightOption, RequestState, Window
from travel_agent.providers.synthetic import SyntheticProvider

_RUNS_DIR = Path(__file__).parent / "runs"
# Profiles that are currently active in llm_routing.yaml.
# demo-qwen demoted 2026-05-16: OpenRouter removed qwen-2.5-72b-instruct:free.
# demo-deepseek-v4 added 2026-05-17: NIM fallback for Groq quota exhaustion.
_PROFILES = ["demo-haiku", "demo-llama", "demo-deepseek-v4"]

_logger = structlog.get_logger(__name__)


def _resolve_client_and_model(profile: str, routing: dict, profile_cfg: dict) -> tuple[object, str]:
    """Return (client, model) for flat or agent-routed profiles."""
    from travel_agent.llm import (  # noqa: PLC0415
        get_llm_client_and_model,
        get_llm_client_for_provider,
    )

    provider = profile_cfg.get("provider", "")
    if "model" in profile_cfg:
        return get_llm_client_for_provider(provider), profile_cfg["model"]
    return get_llm_client_and_model("optimizer", profile)


def _build_nim_fallback(routing: dict) -> tuple[object | None, str]:
    """Return (fallback_client, fallback_model) if NVIDIA_API_KEY is set, else (None, '')."""
    from travel_agent.llm import get_llm_client_for_provider  # noqa: PLC0415

    if not os.environ.get("NVIDIA_API_KEY"):
        return None, ""
    nim_cfg = routing.get("demo-deepseek-v4", {})
    if nim_cfg and "model" in nim_cfg:
        client = get_llm_client_for_provider(nim_cfg["provider"])
        return client, nim_cfg["model"]
    return None, ""


def _make_flight_sets() -> list[dict]:
    """Generate 24 diverse flight scenarios for Pareto evaluation."""
    import contextlib  # noqa: PLC0415
    from datetime import date, timedelta  # noqa: PLC0415

    provider = SyntheticProvider()

    scenarios = []
    routes = [
        ("DEL", "DXB"),
        ("DEL", "SIN"),
        ("BOM", "BKK"),
        ("BOM", "DXB"),
        ("DEL", "KUL"),
        ("DEL", "BKK"),
    ]
    for i, (origin, dest) in enumerate(routes):
        for j in range(4):  # 4 windows per route = 24 total
            start = date(2026, 6, 1) + timedelta(days=j * 7)
            window = Window(start_date=start, end_date=start + timedelta(days=6))
            with contextlib.suppress(Exception):
                flights = provider.get_flights(origin, dest, window)
                if flights:
                    scenarios.append(
                        {
                            "id": f"opt-{i * 4 + j + 1:03d}",
                            "route": f"{origin}-{dest}",
                            "window_start": str(start),
                            "flights": [f.model_dump(mode="json") for f in flights],
                            "n_flights": len(flights),
                        }
                    )

    return scenarios


async def run_profile(profile: str, scenarios: list[dict], dry_run: bool) -> list[dict]:
    """Run all scenarios under one profile. Returns list of result records."""
    if dry_run:
        client = None
        model = "dry-run"
    else:
        try:
            from travel_agent.llm.routing import load_routing_config  # noqa: PLC0415

            routing = load_routing_config()
            if profile not in routing:
                _logger.warning(
                    "eval_profile_skipped",
                    profile=profile,
                    reason="profile not found in llm_routing.yaml (demoted or commented out)",
                )
                return []

            profile_cfg = routing[profile]
            provider = profile_cfg.get("provider", "")
            client, model = _resolve_client_and_model(profile, routing, profile_cfg)

            if provider in TPM_LIMITS:
                tracker = TokenTracker(TPM_LIMITS[provider])
                try:
                    fallback_client, fallback_model = _build_nim_fallback(routing)
                except Exception as fb_exc:
                    _logger.warning("nim_fallback_unavailable", reason=str(fb_exc))
                    fallback_client, fallback_model = None, ""
                if fallback_client is None and profile != "demo-deepseek-v4":
                    _logger.warning(
                        "nim_fallback_disabled",
                        reason="NVIDIA_API_KEY not set — 429s will not fall back to NIM",
                    )
                client = ThrottledLLMClient(
                    client, tracker, fallback=fallback_client, fallback_model=fallback_model
                )
        except Exception as exc:
            _logger.warning("eval_profile_skipped", profile=profile, reason=str(exc))
            return []

    optimizer = OptimizerAgent(client=client, model=model)
    results = []

    for scenario in scenarios:
        flights = [FlightOption.model_validate(f) for f in scenario["flights"]]
        state = RequestState(raw_input="eval", flight_options=flights)
        t0 = time.monotonic()
        try:
            state = await optimizer.run(state)
            latency_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            results.append({**scenario, "profile": profile, "error": str(exc)})
            continue

        archetypes = [a.model_dump(mode="json") for a in state.archetypes]
        results.append(
            {
                **scenario,
                "profile": profile,
                "model": model,
                "archetypes": archetypes,
                "latency_ms": round(latency_ms, 1),
            }
        )

    return results


def save_run(profile: str, records: list[dict]) -> Path:
    _RUNS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    out = _RUNS_DIR / f"{ts}_{profile}.jsonl"
    with out.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    print(f"[{profile}] Saved {len(records)} records -> {out}")
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="Optimizer eval runner")
    parser.add_argument("--profile", action="append", dest="profiles", metavar="PROFILE")
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()

    profiles = _PROFILES if args.all_profiles else (args.profiles or ["demo-haiku"])
    scenarios = _make_flight_sets()
    print(f"Generated {len(scenarios)} flight scenarios")

    for profile in profiles:
        records = await run_profile(profile, scenarios, dry_run=args.dry_run)
        if records:
            save_run(profile, records)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
