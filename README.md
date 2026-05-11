# Agentic Travel Booking System

**Live:** https://agentic-travel-booking-system.vercel.app

A multi-agent B2B layer that takes a natural-language travel request and returns two ranked
packages (best-value, best-experience) across flights and hotels for any 7-day window in the
next 30 days. Designed as a drop-in agent layer for travel platforms — your inventory, our
reasoning.

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, Anthropic SDK |
| Frontend | Next.js 15, React 19, TailwindCSS, shadcn/ui |
| LLM | Multi-provider routing: Ollama (local), OpenRouter/Groq (free cloud), Anthropic (eval baseline) |
| Database | Neon (Postgres), Alembic migrations |
| Cache / Rate limiting | Upstash Redis |
| Auth (tenant) | API key + JWT; Clerk for user sessions |
| Infra | GCP Cloud Run, Vercel, GitHub Actions + WIF |

## Getting Started

**Prerequisites:** WSL2 Ubuntu, Python 3.12, Node 20+.

```bash
# Copy env files and fill in values (see docs/runbooks/cloud-setup.md first)
cp apps/api/.env.example apps/api/.env.local
cp apps/web/.env.example apps/web/.env.local

# Install all dependencies and pre-commit hooks
make setup

# Run API (dev) — requires Phase 1 to be complete
make run-api

# Run web app (dev)
make run-web

# Pre-flight checks
make lint && make typecheck && make test
```

## Architecture

Six specialist agents (Planner, FlightHunter, HotelHunter, Optimizer, Booking, Conversation) + a deterministic coordinator. See:
- `docs/architecture/` — ADRs and system overview
- `docs/runbooks/cloud-setup.md` — provisioning GCP, Neon, Upstash, Vercel
- `plan.md` — phased delivery plan and design decisions

## Open-Source Model Track

Alongside the core SaaS product, this project fine-tunes compact open-source models
(Qwen 2.5 7B/14B) per agent using QLoRA, with the goal of matching frontier performance
on narrow tasks at zero inference cost.

- **Adapters** released on Hugging Face under CC-BY-NC-4.0 (adapter weights only; base model stays Apache 2.0)
- **Eval harness** in `evals/` — golden datasets, judge prompts, runner and scorer
- **Dataset pipeline** in `scripts/dataset/` — diversity matrix, generation, self-critique, human QA
- **Research workspace** in `docs/research/` — experiment log, model cards, benchmark protocol
- **20% of eval golden examples** released publicly as a reproducible benchmark sample (CC-BY-4.0)

See ADRs 0008–0012 in `docs/architecture/adr/` for design decisions.

## License

MIT. See `LICENSE`.
