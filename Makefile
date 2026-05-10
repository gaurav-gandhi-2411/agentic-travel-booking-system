.PHONY: setup test lint typecheck run-api run-web eval-quick eval-full eval-baselines

PYTHON     := python3.12
VENV       := $(HOME)/projects/venv-dealhunter
API_DIR    := apps/api
WEB_DIR    := apps/web

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e "$(API_DIR)[dev]"
	cd $(WEB_DIR) && npm install
	$(VENV)/bin/pre-commit install

test:
	cd $(API_DIR) && $(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check $(API_DIR)/src $(API_DIR)/tests
	cd $(WEB_DIR) && npm run lint

typecheck:
	$(VENV)/bin/mypy $(API_DIR)/src
	cd $(WEB_DIR) && npm run typecheck

run-api:
	$(VENV)/bin/uvicorn travel_agent.api.main:app --reload

run-web:
	cd $(WEB_DIR) && npm run dev

# ── Eval targets ─────────────────────────────────────────────────────────────
# eval-quick: 20 examples per agent, ~2 min. Run before every PR.
eval-quick:
	$(VENV)/bin/python -m evals.run --agent all --mode quick

# eval-full: Full dataset, ~30 min. Nightly CI; >2% regression blocks merge.
eval-full:
	$(VENV)/bin/python -m evals.run --agent all --mode full

# eval-baselines: Frontier models. Requires ANTHROPIC_API_KEY + LLM_ROUTING_PROFILE=eval.
# Never run in CI — manual baseline benchmarks only.
eval-baselines:
	LLM_ROUTING_PROFILE=eval $(VENV)/bin/python -m evals.run --agent all --mode baselines
