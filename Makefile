.PHONY: setup test lint typecheck run-api run-web

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
