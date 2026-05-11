FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Run as non-root.
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --no-create-home appuser
USER appuser

EXPOSE 8080

CMD ["uvicorn", "travel_agent.api.main:app", \
     "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
