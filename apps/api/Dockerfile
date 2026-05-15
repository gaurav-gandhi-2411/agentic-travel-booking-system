# Phase 0 / Stage 0.4 baseline Dockerfile. Installs all declared dependencies
# and packages the minimum travel_agent module needed for the staging-deploy
# workflow to verify the full provisioning chain. Phase 1 expands this for
# the real API surface (routes, middleware, observability wiring).
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
COPY config/ config/
RUN pip install --no-cache-dir .

ENV COORDINATOR_CONFIG_PATH=/app/config/coordinator.yaml
ENV LLM_ROUTING_CONFIG_PATH=/app/config/llm_routing.yaml

# Run as non-root.
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --no-create-home appuser
USER appuser

EXPOSE 8080

CMD ["uvicorn", "travel_agent.api.main:app", \
     "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]
