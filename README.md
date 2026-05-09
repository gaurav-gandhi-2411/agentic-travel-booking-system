# Agentic Travel Booking System

A multi-agent B2B layer that takes a natural-language travel request and returns two ranked
packages (best-value, best-experience) across flights and hotels for any 7-day window in the
next 30 days. Designed as a drop-in agent layer for travel platforms — your inventory, our
reasoning.

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, Anthropic SDK |
| Frontend | Next.js 15, React 19, TailwindCSS, shadcn/ui |
| LLM | Claude Sonnet 4.6 (reasoning) + Haiku 4.5 (parsing) |
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

Five specialist Claude agents + a deterministic coordinator. See:
- `docs/architecture/` — ADRs and system overview
- `docs/runbooks/cloud-setup.md` — provisioning GCP, Neon, Upstash, Vercel
- `plan.md` — phased delivery plan and design decisions

## License

MIT. See `LICENSE`.
