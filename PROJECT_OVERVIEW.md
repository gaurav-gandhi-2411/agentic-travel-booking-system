# DealHunter — Project Overview

**30-second orientation for a new engineer.**

## What it is

DealHunter is an agentic flight-search system that demonstrates how to build a production-grade AI-powered search workflow on **free-tier LLM infrastructure**. The pitch: 99% gross margin on the open-source profile, with paid Anthropic available as an opt-in premium tier.

It is a **coordinator-pattern** system (not autonomous multi-agent). Deterministic Python orchestration drives a Planner → FlightHunter → Optimizer pipeline with SSE streaming to a Next.js frontend. A ConversationManagerAgent (added Phase 2C.4) handles natural-language refinement of existing search results.

## The pipeline

```
User query
    ↓
PlannerAgent         — extract TravelIntent from natural language (Groq/Llama)
    ↓
FlightHunter         — retrieve flights from SyntheticProvider (deterministic)
    ↓
OptimizerAgent       — select Pareto-optimal archetypes + generate explanations (Groq/Llama or GPT-OSS-120B)
    ↓
SSE stream           — events streamed to frontend in real time
    ↓
ConversationManager  — interprets follow-up messages: REFINE / REPLAN / NO_OP (Groq/Llama)
```

The coordinator (`apps/api/src/travel_agent/coordinator/`) drives the pipeline deterministically. Agents are stateless; state lives in `RequestState`.

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Python 3.12, hosted on Google Cloud Run |
| Frontend | Next.js 15 / React 19, hosted on Vercel |
| Cache | Upstash Redis (30-min TTL, keyed by request_id) |
| LLM providers | Groq (Llama 3.3, Qwen3-32B judge), NVIDIA NIM (DeepSeek V4), Anthropic (Haiku opt-in) |
| Observability | Langfuse (LLM call tracing), structlog (structured JSON logging), Prometheus /metrics |
| CI/CD | GitHub Actions, Workload Identity Federation (no service account keys), Artifact Registry |
| Evals | Custom eval harness under `apps/api/evals/`, LLM-as-judge (Qwen3-32B primary), pytest |

## Repo layout

```
agentic-travel-booking-system/
├── apps/
│   ├── api/                          # FastAPI backend (Python 3.12)
│   │   ├── src/travel_agent/         # All source code
│   │   │   ├── agents/               # PlannerAgent, OptimizerAgent, ConversationManagerAgent
│   │   │   ├── api/                  # FastAPI routes: /search, /refine, /health
│   │   │   ├── coordinator/          # Streaming pipeline, RequestState, SSE events
│   │   │   ├── llm/                  # Provider adapters (Groq, Anthropic, NVIDIA NIM)
│   │   │   ├── observability/        # Langfuse tracing, cost pricing table
│   │   │   └── evals/                # Eval harness (optimizer + conversation_manager)
│   │   ├── tests/                    # 485 unit tests, 86% coverage
│   │   ├── config/                   # llm_routing.yaml — LLM profiles
│   │   └── evals/                    # Eval run artifacts, judge cache, reports
│   └── web/                          # Next.js 15 frontend
│       ├── app/                      # Next.js App Router
│       ├── components/demo/          # DemoClient, ProfileToggle, ChatLog, ChatMessage
│       ├── hooks/                    # useSearchStream (SSE consumer)
│       └── lib/                      # event-map.ts, chat-types.ts
├── docs/architecture/adr/            # 26 Architecture Decision Records (0001–0026)
├── .github/workflows/                # ci.yml, deploy-staging.yml, deploy-prod.yml,
│                                     # production-staleness-check.yml
├── CURRENT_STATE.md                  # Primary handoff doc — read before every session
├── WORKFLOW.md                       # Orchestrator working pattern
└── spec.md                           # Current or most recent iteration spec
```

## Active demo profiles

| Profile | Model | Provider | Notes |
|---|---|---|---|
| `demo-llama` | Llama 3.3 70B | Groq | Default nightly eval profile |
| `demo-gpt-oss-120b` | GPT-OSS-120B | Groq | `reasoning_effort: low` required |
| `demo-deepseek-v4` | DeepSeek V4 Flash | NVIDIA NIM | Excluded from nightly (credit pool) |
| `demo-haiku` | Claude Haiku 4.5 | Anthropic | Opt-in only — paid tier |
| `eval-judge-qwen3-32b` | Qwen3-32B | Groq | Eval judge — not a demo profile |
| `eval-judge-sonnet` | Claude Sonnet 4.6 | Anthropic | Fallback judge — costs money |

## Where Phase 2D ended

Phase 2D (May 2026) was a production-hardening phase. Six iterations:

| Iteration | Focus |
|---|---|
| 1 | Audit: both production surfaces found silently non-functional since May 15 |
| 2 | Backend deploy workflow fix, secret management |
| 3 | Backend production deploy v0.5.0 → v0.6.0, canary gate |
| 4 | Frontend production alignment (Vercel freeze + empty env vars fixed) |
| 5 | Staleness guardrail (daily cron, GitHub issue alerts), CI hygiene |
| 6 | Eval rigor: judge_model recording, cross-profile gate, cache poison fix |

**The big finding:** Both production surfaces were completely non-functional from initial setup (May 15) until Phase 2D iterations 3-4 (May 31). Backend was frozen at v0.5.0; frontend had empty `API_BASE_URL` and `DEMO_API_KEY` env vars. Every search since launch had failed silently. See ADR-0023 and ADR-0024.

**Production is now fully current** (backend `00019-liy` at 100%, frontend on main HEAD, staleness guardrail active).

## What Phase 3 would be

Based on the open-issues backlog, the natural next phase is **prompt caching + cost optimization**:
- Issues #33/#34/#35: Planner, Optimizer, and ConversationManager are all below the Anthropic prompt-caching threshold; Phase 2C.4.5 prep work exists but was never activated
- Issue #8: Promote optimizer eval to a blocking CI gate
- Issue #10: Wire Sentry for error aggregation
- B2B SaaS features: multi-tenancy, Postgres RLS, booking/affiliate integration (ADR-0003, ADR-0004)

The system is production-ready for demo/portfolio purposes. Production URLs in CURRENT_STATE.md.
