"""Unit tests for get_engine()'s pooler-mode visibility (ADR-0028).

The absence of a pool-exhaustion error does NOT prove the transaction pooler is
active -- the session-pooler fallback could be silently in effect under load
too light to ever hit its 15-client ceiling either way. get_engine() logs
which pooler port and source env var it resolved, so a canary smoke test can
grep structured logs for proof rather than inferring it from a lack of 500s.

create_async_engine() is lazy (no network I/O until a connection is actually
checked out), so these tests are safe to run against fake hosts.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

import travel_agent.persistence.engine as engine_mod

_RUNTIME_URL = "postgresql://dealhunter_app.myref:supersecretpw@aws-1-ap-south-1.pooler.supabase.com:6543/postgres"
_SESSION_URL = "postgresql://dealhunter_app.myref:supersecretpw@aws-1-ap-south-1.pooler.supabase.com:5432/postgres"


@pytest.fixture(autouse=True)
def _reset_engine_singleton() -> Iterator[None]:
    """get_engine() caches a module-level singleton -- reset it so each test
    observes a fresh construction (and its log line) instead of a cached one."""
    engine_mod._engine = None
    yield
    engine_mod._engine = None


def test_logs_transaction_pooler_port_when_runtime_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL_RUNTIME", _RUNTIME_URL)
    monkeypatch.setenv("DATABASE_URL", _SESSION_URL)
    with patch.object(engine_mod, "_logger") as mock_logger:
        engine_mod.get_engine()
    mock_logger.info.assert_called_once_with(
        "db_engine_configured",
        port=6543,
        pooler_mode="transaction",
        source_env_var="DATABASE_URL_RUNTIME",
    )


def test_logs_session_pooler_port_when_runtime_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the silent-fallback case the smoke test must be able to detect:
    DATABASE_URL_RUNTIME missing/unreadable -> session pooler, port 5432."""
    monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
    monkeypatch.setenv("DATABASE_URL", _SESSION_URL)
    with patch.object(engine_mod, "_logger") as mock_logger:
        engine_mod.get_engine()
    mock_logger.info.assert_called_once_with(
        "db_engine_configured",
        port=5432,
        pooler_mode="session",
        source_env_var="DATABASE_URL",
    )


def test_log_never_includes_credentials_or_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The log call's kwargs must never carry the raw URL, host, username, or password."""
    monkeypatch.setenv("DATABASE_URL_RUNTIME", _RUNTIME_URL)
    with patch.object(engine_mod, "_logger") as mock_logger:
        engine_mod.get_engine()
    call_kwargs = mock_logger.info.call_args.kwargs
    serialized = str(call_kwargs)
    assert "supersecretpw" not in serialized
    assert "dealhunter_app" not in serialized
    assert "pooler.supabase.com" not in serialized
    assert set(call_kwargs) == {"port", "pooler_mode", "source_env_var"}


def test_raises_when_neither_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL_RUNTIME", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL_RUNTIME or DATABASE_URL"):
        engine_mod.get_engine()


def test_singleton_only_logs_once_across_repeated_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL_RUNTIME", _RUNTIME_URL)
    with patch.object(engine_mod, "_logger") as mock_logger:
        engine_mod.get_engine()
        engine_mod.get_engine()
        engine_mod.get_engine()
    assert mock_logger.info.call_count == 1


# ── _pooler_connect_args() -- ADR-0028 addendum, DuplicatePreparedStatementError fix ──


def test_pooler_connect_args_empty_for_non_pooler_host() -> None:
    """Non-Supabase hosts (e.g. local Postgres, Neon) get no connect_args at all --
    the prepared-statement collision is specific to Supavisor's transaction-mode
    multiplexing, not something every Postgres needs to pay for."""
    assert engine_mod._pooler_connect_args("db.example.com") == {}


def test_pooler_connect_args_empty_for_none_host() -> None:
    assert engine_mod._pooler_connect_args(None) == {}


def test_pooler_connect_args_disables_cache_for_supabase_pooler_host() -> None:
    args = engine_mod._pooler_connect_args("aws-1-ap-south-1.pooler.supabase.com")
    assert args["prepared_statement_cache_size"] == 0


def test_pooler_connect_args_disables_raw_asyncpg_cache_too() -> None:
    """Regression guard: prepared_statement_cache_size=0 only covers SQLAlchemy's
    own query-execution path. pool_pre_ping's do_ping() calls fetchrow() directly
    on the underlying asyncpg connection, which uses asyncpg's OWN internal cache
    (raw statement_cache_size, a different setting) -- confirmed live to still
    collide (DuplicatePreparedStatementError / InvalidSQLStatementNameError) if
    this key is left at asyncpg's default of 100."""
    args = engine_mod._pooler_connect_args("aws-1-ap-south-1.pooler.supabase.com")
    assert args["statement_cache_size"] == 0


def test_pooler_connect_args_name_func_produces_globally_unique_names() -> None:
    """The whole point of this addendum: prepared_statement_cache_size=0 ALONE
    still lets asyncpg fall back to its own sequential per-connection-object
    naming (name=None), which is exactly what collided under concurrent load.
    A supplied name_func must generate a fresh, non-colliding name every call,
    not just once at construction time."""
    args = engine_mod._pooler_connect_args("aws-1-ap-south-1.pooler.supabase.com")
    name_func = args["prepared_statement_name_func"]
    assert callable(name_func)
    names = {name_func() for _ in range(1000)}  # type: ignore[operator]
    assert len(names) == 1000
    assert all(n.startswith("__asyncpg_") and n.endswith("__") for n in names)


def test_get_engine_passes_pooler_connect_args_to_create_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end wiring check: get_engine() must actually forward
    _pooler_connect_args()'s output into create_async_engine(), not just compute
    it and drop it -- this is the exact class of bug (right helper, not wired
    in) that let the wrong key silently pass code review before."""
    monkeypatch.setenv("DATABASE_URL_RUNTIME", _RUNTIME_URL)
    with patch.object(engine_mod, "create_async_engine") as mock_create:
        engine_mod.get_engine()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["connect_args"]["prepared_statement_cache_size"] == 0
    assert callable(call_kwargs["connect_args"]["prepared_statement_name_func"])
    assert call_kwargs["pool_recycle"] == engine_mod._POOL_RECYCLE_SECONDS
