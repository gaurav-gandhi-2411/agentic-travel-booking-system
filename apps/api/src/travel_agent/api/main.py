# Phase 0 / Stage 0.4 — minimum FastAPI module required for the staging
# deploy workflow to verify the WIF → AR → Cloud Run chain end-to-end.
# The /health endpoint returns a static dict; the Postgres-backed
# implementation that Cloud Scheduler actually warms lives in Phase 1.
# See plan.md §11 Phase 0 and the runbook §15 verification step.
from fastapi import FastAPI

app = FastAPI(title="Agentic Travel Booking API", version="0.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "0"}
