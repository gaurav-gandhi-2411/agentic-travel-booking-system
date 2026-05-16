# Local Development Guide

## Quick start (all platforms)

```bash
cd apps/api
pip install -e ".[dev]"
pip install pre-commit && pre-commit install
uvicorn travel_agent.api.main:app --reload
```

Unit tests run on any OS:

```bash
cd apps/api && pytest tests/unit/
```

---

## Local development on Windows

Running the FastAPI server locally on Windows hits a known asyncio + httpx + TLS
bug (`[Errno 22] Invalid argument` on outbound HTTPS calls under ProactorEventLoop).
The supported local-server workflow on Windows is **WSL2**.

### Setup (one-time, ~10 min)

1. Install WSL2 with Ubuntu 22.04 or later:
   ```
   wsl --install -d Ubuntu-22.04
   ```

2. Inside WSL2, clone the repo to your WSL home directory — **not** to
   `/mnt/c/...`. File I/O across the WSL boundary is dramatically slower and
   causes hot-reload to miss changes:
   ```bash
   cd ~
   git clone https://github.com/gaurav-gandhi-2411/agentic-travel-booking-system.git
   cd agentic-travel-booking-system
   ```

3. Install Python 3.12 and project tools inside WSL:
   ```bash
   sudo apt install python3.12 python3.12-venv python3-pip
   cd apps/api && pip install -e ".[dev]"
   pip install pre-commit && pre-commit install
   ```

4. Copy your `.env` file into the WSL clone, then start the server:
   ```bash
   cp /mnt/c/Users/<your-user>/ml-projects/agentic-travel-booking-system/apps/api/.env apps/api/.env
   make run-api
   # or: cd apps/api && uvicorn travel_agent.api.main:app --reload
   ```

The server listens on `http://localhost:8000` and is accessible from the Windows
host at the same address.

### Unit tests on Windows (native Python)

`pytest` works fine with the Windows-native Python install. The asyncio bug is
HTTPS-specific — tests that mock the LLM client run on either OS:

```bash
cd apps/api && pytest tests/unit/
```

Only avoid running the FastAPI server or tests that make real outbound TLS calls
(e.g. the integration tests gated on `UPSTASH_REDIS_URL`) from native Windows.

### Why not fix the Windows path?

The fix (`asyncio.WindowsSelectorEventLoopPolicy()`) requires patching uvicorn's
bootstrap, which we'd own and maintain indefinitely. WSL2 is a one-time setup
and matches the production environment (Cloud Run runs Linux). Matching prod
locally has independent benefits (same file paths, same shell, same
`gcloud`/`gh` CLI behaviour).

---

## Makefile targets

From the repo root:

| Target | What it does |
|---|---|
| `make setup` | Create venv, install deps, install pre-commit hooks |
| `make run-api` | Start uvicorn in reload mode (use inside WSL on Windows) |
| `make run-web` | Start Next.js dev server |
| `make test` | Run pytest |
| `make lint` | ruff check + npm lint |
| `make typecheck` | mypy + npm typecheck |
| `make eval-quick` | 20-example planner golden set, ~2 min |
| `make eval-full` | Full dataset, ~30 min |
