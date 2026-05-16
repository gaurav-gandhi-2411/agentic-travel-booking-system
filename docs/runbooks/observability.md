# Observability Runbook

## Reading a trace

Each `/search` request maps to one Langfuse trace, named `search`, with `request_id`
stored in trace metadata.

The trace structure:

```
search (span)
+-- planner_chat (generation)          -- intent extraction LLM call
+-- optimizer_explain/best-value       -- explanation LLM call
+-- optimizer_explain/best-experience  -- explanation LLM call
+-- optimizer_compare (generation)     -- comparison LLM call
```

Each `/refine` request creates a separate `refine` span linked via `session_id=request_id`
to the originating search trace.

---

## Common debug patterns

### "User reports stuck search"

1. Get the `request_id` from the client (check `X-Request-ID` response header or the
   `done` event payload: `{"type": "done", "request_id": "..."}`).
2. In Langfuse dashboard: Traces > search by metadata `request_id`.
3. Inspect which child span is missing or has high latency:
   - `planner_chat` missing: planner failed before the LLM call — check structlog for
     `"PlannerAgent requires non-empty state.raw_input"` or LLM key errors.
   - `optimizer_explain` present but `done` event never arrived: stream connection
     dropped (client disconnect or ALB timeout).
   - `optimizer_compare` missing: compare call failed silently (check for tool-call
     parse errors in logs).

### "Costs spiking unexpectedly"

1. Langfuse dashboard > Analytics > Daily cost by model.
2. Check if a profile is using `claude-sonnet-4-6` instead of `claude-haiku-4-5`
   (config regression in `llm_routing.yaml`).
3. Check `cache_read_tokens` in generation metadata — if 0 on repeated identical
   queries, prompt caching is not activating (system prompt below 1024-token threshold
   for Haiku, or TTL expired).

### "Traces not appearing"

1. Check startup log for `observability enabled` vs `observability disabled — set
   LANGFUSE_PUBLIC_KEY to enable`.
2. Verify `LANGFUSE_PUBLIC_KEY` is set in Cloud Run environment variables (not just
   `.env` — the container doesn't mount `.env`).
3. Verify `LANGFUSE_HOST=https://cloud.langfuse.com` (EU region default).
4. Check that `lf.flush()` is being called — happens automatically at:
   - End of each `_sse_generator()` call (after the stream completes)
   - Application shutdown in the FastAPI lifespan

### "Redis cache health check failing"

The `/health` endpoint pings the cache backend and returns `{"cache": "degraded"}` if
the ping fails. In `APP_MODE=prod` this returns HTTP 503.

1. Check `UPSTASH_REDIS_URL` is set correctly: `rediss://default:<token>@<host>:<port>`
   (note: `rediss://` with double-s = TLS, required for Upstash standard protocol).
2. Test connectivity: `redis-cli -u "$UPSTASH_REDIS_URL" ping` should return `PONG`.
3. If URL is unset or empty, the API silently falls back to in-memory LRU cache (no
   cross-worker sharing, but functional for single-worker deployments).

---

## Cost model (as of 2026-05-16)

| Profile | LLM | Input $/Mtok | Output $/Mtok | Typical cost/search |
|---|---|---|---|---|
| demo-haiku | claude-haiku-4-5 | $0.80 | $4.00 | ~$0.001 |
| demo-llama | llama-3.3-70b | $0.00 | $0.00 | $0 (free tier) |
| demo-qwen | qwen-2.5-72b | $0.00 | $0.00 | $0 (free tier) |

Cost telemetry is reported per LLM call via `structlog.info("llm_call", cost_usd=...)`.
Langfuse generation metadata includes `cost_usd`, `cache_read_tokens`, and
`cache_write_tokens` for Anthropic calls.

Update `src/travel_agent/observability/pricing.py` quarterly to keep rates current.
