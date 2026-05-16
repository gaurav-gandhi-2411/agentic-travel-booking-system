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

## Observability

Traces are sent to [Langfuse Cloud](https://cloud.langfuse.com) when `LANGFUSE_PUBLIC_KEY` is set.

To enable locally:
1. Create a free account at cloud.langfuse.com
2. Create a project `dealhunter-prod`
3. Copy Public Key and Secret Key to your `.env`:
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=https://cloud.langfuse.com
   ```
4. Restart the API — traces appear under the project dashboard within seconds.

Each `/search` request creates one Langfuse trace with:
- `planner_chat` generation (model, tokens, latency, cost)
- `optimizer_explain` generation x2 (best-value + best-experience)
- `optimizer_compare` generation x1 (comparison text)
- `cost_usd` in each generation's metadata

If `LANGFUSE_PUBLIC_KEY` is absent or empty, tracing is silently disabled — no warnings
in production, no pipeline breakage. See `docs/runbooks/observability.md` for ops guidance.

## Known Limitations

- Sentry error alerting: deferred to Phase 2c (SENTRY_DSN needs provisioning)
- Live optimizer eval baseline: requires ANTHROPIC_API_KEY + GROQ_API_KEY + OPENROUTER_API_KEY
  (dry-run baseline with 100% label correctness is committed in `evals/optimizer/runs/`)

## Architecture

ADRs and architecture notes live in [`docs/architecture/adr/`](../../docs/architecture/adr/).
