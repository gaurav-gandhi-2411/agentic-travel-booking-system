FROM python:3.12-slim

WORKDIR /app

# Install deps before copying source for layer caching.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

# Copy application source.
COPY src/ src/

# Run as non-root.
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --no-create-home appuser
USER appuser

EXPOSE 8080

# apps/api/src/travel_agent/api/main.py is created in Phase 1.
CMD ["uvicorn", "travel_agent.api.main:app", \
     "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
