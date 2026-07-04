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
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
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


def _make_route_pool(intent: TravelIntent) -> list[FlightOption]:
    """Return the synthetic pool with each flight's route and cabin substituted
    from the planned intent. Prices, stop counts, and departure times are
    preserved so the optimizer always ranks the same Pareto frontier; only
    the BOM→NRT placeholders are replaced with the case's actual route."""
    return [
        f.model_copy(update={
            "origin_iata": intent.origin_iata,
            "destination_iata": intent.destination_iata,
            "cabin_class": intent.cabin_class,
        })
        for f in SYNTHETIC_REFINE_POOL
    ]


@dataclass
class _Agents:
    planner: PlannerAgent
    conv: ConversationManagerAgent
    opt: OptimizerAgent


def _load_golden() -> list[dict[str, Any]]:
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def _resolve_agents(profile: str, *, use_fallback: bool = True) -> _Agents:
    """Instantiate the three agents for the given routing profile.

    use_fallback=True (default) wires planner/optimizer/conversation through the
    profile's fallback_chain (llm_routing.yaml) so a Groq TPD wall doesn't block
    generation -- see spec.md. Pass use_fallback=False for the AUTHORITATIVE Wave 2
    baseline: that run must stay single-model-clean (see evals/wave2/README.md
    "Fallback and the authoritative baseline"), since a case served by a different,
    weaker free model isn't comparable to one served by the configured model.
    """
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
        # Flat profile — same client + model for every agent; no fallback_chain support.
        client = get_llm_client_for_provider(cfg["provider"])
        model: str = cfg["model"]
        return _Agents(
            planner=PlannerAgent(client=client, model=model),
            conv=ConversationManagerAgent(client=client, model=model, extra_params=extra_params),
            opt=OptimizerAgent(client=client, model=model),
        )

    planner_client, planner_model = get_llm_client_and_model(
        "planner", profile, use_fallback=use_fallback
    )
    conv_client, conv_model = get_llm_client_and_model(
        "conversation", profile, use_fallback=use_fallback
    )
    opt_client, opt_model = get_llm_client_and_model(
        "optimizer", profile, use_fallback=use_fallback
    )
    return _Agents(
        planner=PlannerAgent(client=planner_client, model=planner_model),
        conv=ConversationManagerAgent(client=conv_client, model=conv_model),
        opt=OptimizerAgent(client=opt_client, model=opt_model),
    )


def _reuse_cached(record: dict[str, Any], cached: dict[str, Any], keys: tuple[str, ...]) -> None:
    for k in keys:
        record[k] = cached[k]


async def _run_or_reuse_planner(
    record: dict[str, Any],
    case: dict[str, Any],
    agents: _Agents,
    cached: dict[str, Any] | None,
) -> None:
    """Reuse a prior successful planner call from --resume-from, or call fresh.

    Reuse (not re-spending Groq tokens) is what makes splitting a run across
    multiple clean TPD windows token-frugal.
    """
    if cached is not None and cached.get("intent") is not None:
        _reuse_cached(
            record,
            cached,
            ("latency_ms_planner", "intent", "intent_error", "served_model_planner"),
        )
        return
    t0 = time.monotonic()
    try:
        state = RequestState(raw_input=case["query"])
        result = await agents.planner.run(state, today=date.fromisoformat(case["today"]))
        record["latency_ms_planner"] = round((time.monotonic() - t0) * 1000, 1)
        record["served_model_planner"] = result.served_model.get("planner")
        if result.intent is None:
            record["intent_error"] = "planner returned None intent"
        else:
            record["intent"] = result.intent.model_dump(mode="json")
    except Exception as exc:
        record["latency_ms_planner"] = round((time.monotonic() - t0) * 1000, 1)
        record["intent_error"] = str(exc)
        _safe = str(exc).encode("ascii", "replace").decode()
        _logger.warning("planner_error", case_id=case["id"], error=_safe)


async def _run_or_reuse_refine(
    record: dict[str, Any],
    case: dict[str, Any],
    agents: _Agents,
    cached: dict[str, Any] | None,
) -> None:
    """Reuse rule mirrors the planner's — see _run_or_reuse_planner."""
    if not case.get("refine") or record["intent"] is None:
        return
    if cached is not None and cached.get("refine_classified") is not None:
        _reuse_cached(
            record,
            cached,
            (
                "latency_ms_conversation",
                "refine_classified",
                "refine_error",
                "served_model_conversation",
            ),
        )
        return
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
        record["served_model_conversation"] = refine_state.served_model.get("conversation")
        record["refine_classified"] = classified.model_dump(mode="json")
    except Exception as exc:
        record["latency_ms_conversation"] = round((time.monotonic() - t0) * 1000, 1)
        record["refine_error"] = str(exc)
        _safe = str(exc).encode("ascii", "replace").decode()
        _logger.warning("refine_error", case_id=case["id"], error=_safe)


async def _run_or_reuse_optimizer(
    record: dict[str, Any],
    case: dict[str, Any],
    agents: _Agents,
    cached: dict[str, Any] | None,
    *,
    run_optimizer: bool,
) -> None:
    """Reuse rule mirrors the planner's — this is what lets the 93 optimizer
    calls be split across multiple clean TPD windows via --resume-from instead
    of restarting from zero each window."""
    if not run_optimizer or record["intent"] is None:
        return
    if cached is not None and cached.get("optimizer_archetypes") is not None:
        _reuse_cached(
            record,
            cached,
            (
                "latency_ms_optimizer",
                "optimizer_archetypes",
                "optimizer_error",
                "served_model_optimizer",
                "model_optimizer",
            ),
        )
        return
    intent = TravelIntent.model_validate(record["intent"])
    opt_state = RequestState(
        raw_input=case["query"],
        intent=intent,
        flight_options=_make_route_pool(intent),
    )
    t0 = time.monotonic()
    try:
        opt_result = await agents.opt.run(opt_state, today=date.fromisoformat(case["today"]))
        record["latency_ms_optimizer"] = round((time.monotonic() - t0) * 1000, 1)
        record["optimizer_archetypes"] = [a.model_dump(mode="json") for a in opt_result.archetypes]
        optimizer_served = {
            k: v for k, v in opt_result.served_model.items() if k.startswith("optimizer")
        }
        record["served_model_optimizer"] = optimizer_served or None
    except Exception as exc:
        record["latency_ms_optimizer"] = round((time.monotonic() - t0) * 1000, 1)
        record["optimizer_error"] = str(exc)
        # Sanitize for terminals that can't encode full Unicode (e.g. Windows cp1252)
        safe_err = str(exc).encode("ascii", "replace").decode()
        _logger.warning("optimizer_error", case_id=case["id"], error=safe_err)


async def _generate_one(
    case: dict[str, Any],
    agents: _Agents,
    profile: str,
    *,
    run_optimizer: bool,
    cached: dict[str, Any] | None = None,
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
        # Fallback-chain transparency (spec.md) — the model that actually served
        # each call, vs. model_planner/model_optimizer above (the profile's
        # CONFIGURED model). They differ only when a Groq->OpenRouter fallback
        # fired mid-case. fallback_used=True flags the case as not directly
        # comparable to a pure single-model baseline.
        "served_model_planner": None,
        "served_model_conversation": None,
        "served_model_optimizer": None,
        "fallback_used": False,
    }

    await _run_or_reuse_planner(record, case, agents, cached)
    await _run_or_reuse_refine(record, case, agents, cached)
    await _run_or_reuse_optimizer(record, case, agents, cached, run_optimizer=run_optimizer)

    record["fallback_used"] = _did_fallback(record)
    return record


def _did_fallback(record: dict[str, Any]) -> bool:
    """True if any call in this case was served by a model other than the
    profile's configured model — i.e. a Groq->OpenRouter fallback fired."""
    served_planner = record["served_model_planner"]
    if served_planner and served_planner != record["model_planner"]:
        return True
    served_conversation = record["served_model_conversation"]
    if served_conversation and served_conversation != record["model_conversation"]:
        return True
    served_optimizer: dict[str, str] | None = record["served_model_optimizer"]
    return bool(
        served_optimizer
        and record["model_optimizer"]
        and any(v != record["model_optimizer"] for v in served_optimizer.values())
    )


def _load_resume_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Load a prior run's records keyed by case id, for --resume-from.

    Lets a later window reuse already-succeeded planner/refine/optimizer calls
    instead of re-spending Groq tokens on them — the mechanism that makes
    splitting a run across multiple clean TPD windows token-frugal.
    """
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {r["id"]: r for r in records}


@dataclass(frozen=True)
class GenerationOptions:
    run_optimizer: bool
    use_fallback: bool = True
    resume_from: Path | None = None


async def generate_all(
    cases: list[dict[str, Any]],
    profile: str,
    limit: int | None,
    options: GenerationOptions,
) -> list[dict[str, Any]]:
    agents = _resolve_agents(profile, use_fallback=options.use_fallback)
    planner_model: str = agents.planner._model  # type: ignore[attr-defined]

    if limit is not None:
        cases = cases[:limit]

    resume_from = options.resume_from
    run_optimizer = options.run_optimizer
    resume_cache = _load_resume_cache(resume_from) if resume_from is not None else {}

    opt_tag = " + optimizer" if run_optimizer else " (no optimizer)"
    fb_tag = "fallback ON" if options.use_fallback else "fallback OFF (single-model-clean)"
    resume_tag = f", resuming from {resume_from.name}" if resume_from is not None else ""
    print(
        f"Generating {len(cases)} cases — profile={profile}, planner={planner_model}"
        f"{opt_tag}, {fb_tag}{resume_tag}"
    )

    records: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        print(f"  [{i:>2}/{len(cases)}] {case['id']}  {case['query'][:55]}")
        record = await _generate_one(
            case,
            agents,
            profile,
            run_optimizer=run_optimizer,
            cached=resume_cache.get(case["id"]),
        )
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

    _print_fallback_summary(records)
    return records


def _print_fallback_summary(records: list[dict[str, Any]]) -> None:
    """Provider-transparency note (spec.md) — flag cases served by a fallback
    model so a mixed-provider run is never mistaken for a clean single-model
    baseline."""
    mixed = [r["id"] for r in records if r.get("fallback_used")]
    if not mixed:
        return
    print(
        f"\n*** {len(mixed)}/{len(records)} cases used the OpenRouter fallback "
        f"(Gemma-4-31B) instead of the configured Groq model: {mixed} ***\n"
        "This run is NOT directly comparable to a pure single-model baseline -- "
        "a case scoring lower may reflect the fallback model, not the agent. "
        "For the AUTHORITATIVE Wave 2 baseline, re-run with --no-fallback once "
        "Groq TPD resets."
    )


def save_run(profile: str, records: list[dict[str, Any]]) -> Path:
    _RUNS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    out = _RUNS_DIR / f"{ts}_{profile}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    print(f"\nSaved {len(records)} records -> {out}")
    return out


# Estimated Groq token consumption for a full 31-case demo-llama run.
# Each case: 1 planner + 3 optimizer LLM calls ≈ 1,123 tokens/call (measured).
# 31 cases x 4 calls + 3 refine extra = 127 calls x 1,123 ≈ 143,000 tokens.
# Groq llama-3.3-70b-versatile TPD is 100,000 — the full run CANNOT complete in one
# day without splitting the run or changing the optimizer model. See README.md.
# Confirmed empirically 2026-07-05: optimizer alone (31 x 3 = 93 calls, ~104k tokens)
# already exceeds a fully-fresh day's 100k ceiling on its own -- splitting the
# optimizer step itself across >=2 clean windows (--resume-from) is required even
# starting from zero prior usage, not just when the day is partially consumed.
_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_PROBE_MODEL = "llama-3.3-70b-versatile"
_HTTP_TOO_MANY_REQUESTS = 429
# Minimum remaining TPD we require before starting; set to the full daily limit so
# any prior consumption stops the run. A partial baseline is not usable.
_GROQ_MIN_REMAINING = 100_000
# Groq's chat-completions API does NOT return x-ratelimit-*-tokens-day response
# headers on a 200 -- only per-MINUTE headers (x-ratelimit-*-tokens, no "-day"
# suffix). The daily bucket's Used/Limit numbers are only ever revealed inside a
# 429's error body, e.g. "Rate limit reached for model ... on tokens per day
# (TPD): Limit 100000, Used 99814, Requested 20037. Please try again in 4h45m...".
# Confirmed empirically 2026-07-05 (direct curl against the live API) -- a probe
# that reads response headers for daily figures always sees them absent and
# silently reports remaining=0/limit=0, a false "TPD exhausted" regardless of
# actual state. Fix: deliberately over-request via max_tokens so Groq's own
# pre-generation budget check either 429s with the exact Used/Limit or succeeds
# (confirmed: a 429's "Requested" field matches max_tokens + prompt_tokens, not
# actual completion length, so this never wastes real budget on the reject path;
# on the accept path actual usage stays tiny too, since the model naturally
# stops after a few tokens for a trivial "Say OK" prompt).
#
# Capped at the MODEL's own max_tokens ceiling (32768 for llama-3.3-70b-versatile
# — confirmed empirically: Groq 400s "max_tokens must be <= 32768" for anything
# above that, independent of the rate limiter). Since 32768 < _GROQ_MIN_REMAINING
# (100_000), a single probe call can only ever confirm a LOWER BOUND on daily
# remaining (">= ~32.7k" on success), never the full 100k — see probe_groq_tpd's
# docstring.
_GROQ_PROBE_MAX_TOKENS = 32768 - 50  # headroom for ~37-50 prompt tokens
_TPD_ERROR_RE = re.compile(
    r"on tokens per day \(TPD\): Limit (\d+), Used (\d+), Requested (\d+)\."
    r".*?try again in ([0-9hm.]*[0-9]s)",  # must end in "s" -- stops before the
    re.DOTALL,  # sentence-ending "." after the unit, e.g. "...28.8s." Need more..."
)
_TPM_ERROR_RE = re.compile(r"on tokens per minute \(TPM\)")


def _parse_groq_429(body: str, print_fn) -> dict[str, int | str]:
    if _TPM_ERROR_RE.search(body):
        print_fn(f"[probe] 429 TPM (per-minute, transient) — {body[:200]}")
        return {"status": "429_tpm", "detail": body}
    m = _TPD_ERROR_RE.search(body)
    if not m:
        return {"status": "error", "detail": f"429 but couldn't parse body: {body[:300]}"}
    tpd_limit, used, requested, reset_in = int(m[1]), int(m[2]), int(m[3]), m[4]
    remaining = tpd_limit - used
    print_fn(
        f"[probe] Groq TPD  remaining={remaining:,}  used={used:,}  limit={tpd_limit:,}  "
        f"(requested {requested:,}, reset in {reset_in})  model={_GROQ_PROBE_MODEL}"
    )
    return {
        "status": "429_tpd",
        "remaining": remaining,
        "limit": tpd_limit,
        "used": used,
        "reset_in": reset_in,
    }


async def probe_groq_tpd(print_fn=print) -> dict[str, int | str]:
    """Probe real Groq TPD headroom for llama-3.3-70b-versatile.

    Deliberately over-requests (at the model's own max_tokens ceiling, 32768) so
    Groq's own pre-generation budget check either 429s with the exact Used/Limit/
    reset-eta (daily bucket has less than ~32.7k left) or succeeds (daily bucket
    has at least ~32.7k) -- see the module comment above for why this is the only
    reliable signal (response headers never carry daily figures) and why it's
    capped there (Groq 400s above 32768, independent of the rate limiter).

    Returns a dict with keys depending on status:
      'ok'       — remaining >= _GROQ_PROBE_MAX_TOKENS (~32.7k; a LOWER BOUND only,
                   not exact -- a single call can never confirm the full 100k)
      '429_tpd'  — daily bucket has less than ~32.7k left; remaining/limit/used/
                   reset_in are exact
      '429_tpm'  — hit the per-minute bucket instead (transient, not a daily block;
                   retry shortly, does not mean the daily budget is exhausted)
      'error'    — request failed outright (bad key, network, unparseable 429 body)
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return {"status": "error", "detail": "GROQ_API_KEY not set"}

    payload = {
        "model": _GROQ_PROBE_MODEL,
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": _GROQ_PROBE_MAX_TOKENS,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _GROQ_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=30.0,
            )
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}

    if resp.status_code == _HTTP_TOO_MANY_REQUESTS:
        return _parse_groq_429(resp.text, print_fn)

    try:
        resp.raise_for_status()
    except Exception as exc:
        return {"status": "error", "detail": f"HTTP {resp.status_code}: {exc}"}

    print_fn(
        f"[probe] Groq TPD  remaining>={_GROQ_PROBE_MAX_TOKENS:,} (exact figure not "
        f"exposed on success) model={_GROQ_PROBE_MODEL}"
    )
    return {"status": "ok", "remaining": _GROQ_PROBE_MAX_TOKENS}


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
    parser.add_argument(
        "--probe", action="store_true",
        help=(
            "Probe Groq TPD headroom and exit (deliberately over-requests to force a "
            "429 whose error body carries the real Used/Limit -- response headers "
            "never do)"
        ),
    )
    parser.add_argument(
        "--no-fallback", action="store_true",
        help=(
            "Disable the OpenRouter fallback chain -- forces every case through the "
            "profile's configured model only. REQUIRED for the authoritative Wave 2 "
            "baseline (mixed-provider runs aren't comparable to a single-model "
            "baseline); the default (fallback ON) is for resilient/non-blocking "
            "reruns through a Groq TPD wall. See README.md."
        ),
    )
    parser.add_argument(
        "--resume-from", type=Path, default=None,
        help=(
            "Prior run JSONL to reuse cached planner/refine/optimizer output from -- "
            "any case whose call already succeeded there is never re-spent. This is "
            "what makes splitting a 100k-TPD-exceeding run (see README's optimizer-"
            "alone-exceeds-100k note) across multiple clean windows token-frugal: "
            "run --no-optimizer first (fits in one window), then repeat with "
            "--resume-from on each subsequent clean window until every case's "
            "optimizer_archetypes is populated."
        ),
    )
    args = parser.parse_args()

    # Loaded unconditionally (not just --probe): the default fallback-ON path now
    # also constructs an OpenRouterAdapter per spec.md, so OPENROUTER_API_KEY must
    # be present alongside GROQ_API_KEY.
    from dotenv import find_dotenv, load_dotenv  # noqa: PLC0415

    load_dotenv(
        find_dotenv(usecwd=True) or r"C:\Users\gaura\ml-projects\agentic-travel-booking-system\.env"
    )

    if args.probe:
        result = await probe_groq_tpd()
        if result["status"] == "error":
            print(f"STOP — probe failed: {result.get('detail')}")
            return 1
        if result["status"] == "429_tpm":
            print(
                "Hit the per-MINUTE bucket, not the daily one — transient, unrelated to "
                "TPD headroom. Retry the probe in a few seconds."
            )
            return 1
        if result["status"] == "429_tpd":
            remaining = result["remaining"]
            print(
                f"STOP — insufficient daily headroom: {remaining:,} remaining "
                f"(used {result['used']:,}/{result['limit']:,}), need "
                f"{_GROQ_MIN_REMAINING:,} for a fully-fresh day. Resets gradually "
                f"(rolling 24h window, not a fixed clock) — retry in {result['reset_in']}.\n"
                "NOTE: optimizer alone (31 cases x 3 calls, ~104k tokens) exceeds a "
                "fully-fresh day's 100k ceiling on its own — even at remaining=100000 "
                "this must be split across >=2 clean windows via --resume-from. "
                "See evals/wave2/README.md."
            )
            return 1
        print(
            f"OK — at least {_GROQ_PROBE_MAX_TOKENS:,} tokens available (a lower bound; "
            "Groq's own max_tokens ceiling of 32768 means no single call can confirm "
            "more than that, even on a fully-fresh 100k day). Note: optimizer alone "
            "(~104k) still exceeds one day's 100k ceiling regardless — plan on "
            "--resume-from across >=2 windows."
        )
        return 0

    cases = _load_golden()
    print(f"Loaded {len(cases)} cases from {_GOLDEN_FILE.name}")

    records = await generate_all(
        cases,
        args.profile,
        args.limit,
        GenerationOptions(
            run_optimizer=not args.no_optimizer,
            use_fallback=not args.no_fallback,
            resume_from=args.resume_from,
        ),
    )
    save_run(args.profile, records)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
