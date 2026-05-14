"""Tests for FastAPI application entry point — health endpoint and lifespan guard."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from travel_agent.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "phase": "C"}


def test_health_response_includes_request_id(client: TestClient) -> None:
    resp = client.get("/health")
    assert "x-request-id" in resp.headers


def test_health_echoes_supplied_request_id(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "my-trace-id"})
    assert resp.headers["x-request-id"] == "my-trace-id"


def test_health_generates_request_id_when_absent(client: TestClient) -> None:
    resp = client.get("/health")
    req_id = resp.headers.get("x-request-id", "")
    assert len(req_id) == 36  # UUID4 format: 8-4-4-4-12


def test_lifespan_guard_raises_eval_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup fails when profile=eval and ANTHROPIC_API_KEY is unset."""
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "eval")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"), TestClient(app):
        pass


def test_lifespan_guard_raises_prod_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup fails when profile=prod and ANTHROPIC_API_KEY is unset."""
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "prod")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"), TestClient(app):
        pass


def test_lifespan_guard_local_no_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """local profile starts cleanly without ANTHROPIC_API_KEY."""
    monkeypatch.setenv("LLM_ROUTING_PROFILE", "local")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with TestClient(app) as c:
        resp = c.get("/health")
    assert resp.status_code == 200


def test_load_dotenv_makes_env_vars_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_dotenv() reads vars from a .env file into os.environ."""
    import os

    from dotenv import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("TEST_DOTENV_SENTINEL=hello_from_dotenv\n")

    monkeypatch.delenv("TEST_DOTENV_SENTINEL", raising=False)
    load_dotenv(dotenv_path=env_file, override=True)

    assert os.environ.get("TEST_DOTENV_SENTINEL") == "hello_from_dotenv"
