# Agentic Travel Booking System — API

Multi-agent reasoning layer for travel platforms. Production API built on FastAPI.
See the repository root README and `plan.md` for full architecture.

## Status

Phase 0 / Stage 0.4 — infrastructure provisioning complete. This service is a
placeholder shell; the actual API surface (agents, routes, tools) lands in Phase 1.

## Development

### Environment variables

**Local dev** — copy `.env.example` to `.env` and fill in the values. The app
calls `load_dotenv()` at startup, so the file is picked up automatically when
you run `uvicorn` from any directory under `apps/api/`.

**Production (Cloud Run)** — set vars via Cloud Run service config / Secret Manager.
No `.env` file is present in the container image, so `load_dotenv()` is a no-op
and all values come from the injected environment.

| Variable | Required for | Description |
|---|---|---|
| `APP_MODE` | always | `synthetic` (default) or `demo` |
| `LLM_ROUTING_PROFILE` | always | `local`, `eval`, or `prod` |
| `DEMO_API_KEY` | `APP_MODE=demo` | Shared secret checked by DemoAuthMiddleware |
| `ANTHROPIC_API_KEY` | `LLM_ROUTING_PROFILE=eval\|prod` | Anthropic API key |
| `AVIASALES_API_KEY` | `APP_MODE=demo` | Aviasales partner API key |
| `AVIASALES_PARTNER_ID` | `APP_MODE=demo` | Aviasales partner marker string |

See `plan.md` §11 (Phased Delivery) for full local dev setup.

## Architecture

ADRs and architecture notes live in [`docs/architecture/adr/`](../../docs/architecture/adr/).
