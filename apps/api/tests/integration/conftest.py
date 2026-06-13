"""Integration test fixtures: real Postgres container via testcontainers.

One Postgres 16 container is spun up per test session. Alembic migrations run
once (session-scoped, synchronous). Each test gets a fresh engine and a fresh
AsyncSession, both function-scoped, so the async engine lives entirely within
the test's own event-loop lifetime. The session is rolled back after each test.

RLS note: the testcontainers default user ('test') is a superuser, and Postgres
superusers bypass RLS even when FORCE ROW LEVEL SECURITY is set. To test actual
RLS enforcement we create an 'app_role' non-superuser and grant it the minimum
permissions needed. The rls_session fixture yields a session that has switched
roles to 'app_role', so queries are subject to RLS policies.
"""

from __future__ import annotations

import os

import alembic.command
import alembic.config
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text
from testcontainers.postgres import PostgresContainer

from travel_agent.persistence.schema import DB_SCHEMA

# Every test connection must select DealHunter's dedicated schema (migrations create
# all objects there, not in public). Set on the engine so unqualified queries resolve.
_SEARCH_PATH = {"server_settings": {"search_path": DB_SCHEMA}}

# Non-superuser role used for RLS-enforced queries in tests.
# The role is created with LOGIN + PASSWORD so app_role_session can connect
# directly — matching the production Cloud SQL posture (direct-login non-superuser,
# not superuser + SET ROLE). SET ROLE from a superuser connection masks the
# INSERT...RETURNING FORCE-RLS bug that direct-login exposes.
_APP_ROLE = "app_role"
_APP_ROLE_PASSWORD = "app_role_test_password"  # test-container only, never prod  # noqa: S105


# ---------------------------------------------------------------------------
# Session-scoped sync: container + alembic (no async, no loop lifetime issues)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container() -> PostgresContainer:  # type: ignore[return]
    """Start a Postgres 16 container and yield it for the entire test session."""
    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg


@pytest.fixture(scope="session")
def asyncpg_url(postgres_container: PostgresContainer) -> str:
    """Run Alembic migrations against the container and return the asyncpg URL.

    Alembic's env.py calls asyncio.run() internally; it finishes and closes its
    own event loop before any test loop starts, so there is no loop conflict.
    """
    plain_url: str = postgres_container.get_connection_url()  # postgresql://...
    pg_url: str = plain_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )

    old_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = plain_url
    try:
        cfg = alembic.config.Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", pg_url)
        alembic.command.upgrade(cfg, "head")
    finally:
        if old_db_url is not None:
            os.environ["DATABASE_URL"] = old_db_url
        else:
            os.environ.pop("DATABASE_URL", None)

    return pg_url


# ---------------------------------------------------------------------------
# Function-scoped async: engine + RLS role per test
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_engine(asyncpg_url: str) -> AsyncEngine:  # type: ignore[return]
    """Create a function-scoped AsyncEngine and set up the app_role for RLS tests.

    Creates a non-superuser 'app_role' role (if it doesn't exist) and grants it
    SELECT/INSERT/UPDATE/DELETE on the tenancy tables. Superusers bypass RLS in
    Postgres (even with FORCE), so RLS tests must run as this non-privileged role.
    """
    engine = create_async_engine(
        asyncpg_url, echo=False, pool_pre_ping=False, connect_args=_SEARCH_PATH
    )
    async with engine.begin() as conn:
        # Create app_role once; IF NOT EXISTS avoids errors on repeated calls.
        # _APP_ROLE is a module-level constant ("app_role"), not user input.
        # asyncpg requires each statement to be executed separately.
        await conn.execute(
            text(
                f"DO $$ BEGIN "
                f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') "
                f"  THEN CREATE ROLE {_APP_ROLE} NOINHERIT LOGIN PASSWORD '{_APP_ROLE_PASSWORD}'; "
                f"  END IF; "
                f"END $$"
            )
        )
        # Least privilege, schema-scoped: USAGE on the dedicated schema only, plus DML
        # on its two tables. No privileges on public.
        await conn.execute(text(f"GRANT USAGE ON SCHEMA {DB_SCHEMA} TO {_APP_ROLE}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DB_SCHEMA}.tenants TO {_APP_ROLE}")
        )
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DB_SCHEMA}.api_keys TO {_APP_ROLE}")
        )

    yield engine  # type: ignore[misc]
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(db_engine: AsyncEngine) -> AsyncSession:  # type: ignore[return]
    """Yield an AsyncSession (as the superuser) rolled back after each test."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session  # type: ignore[misc]
        await session.rollback()


@pytest_asyncio.fixture
async def rls_session(db_engine: AsyncEngine) -> AsyncSession:  # type: ignore[return]
    """Yield an AsyncSession running as app_role (non-superuser) for RLS tests.

    Postgres RLS is enforced for non-superuser roles. All queries through this
    session respect the api_keys_rls_isolation and tenants_rls_isolation policies.
    The role switch is local to the transaction (SET LOCAL ROLE ... within begin).
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session  # type: ignore[misc]
        await session.rollback()


@pytest_asyncio.fixture
async def app_role_session(
    asyncpg_url: str,
    db_engine: AsyncEngine,  # db_engine ensures the role exists first
) -> AsyncSession:  # type: ignore[return]
    """AsyncSession connecting DIRECTLY as app_role (LOGIN non-superuser).

    Matches the production Cloud SQL posture: a non-superuser LOGIN role connecting
    directly, not a superuser connection that then does SET ROLE. The previous
    SET ROLE implementation masked the INSERT...RETURNING FORCE-RLS bug because
    PostgreSQL treats a superuser-origin SET ROLE session differently from a
    direct-login non-superuser session for RETURNING visibility under FORCE RLS.

    The db_engine dependency ensures app_role (LOGIN PASSWORD) is created before
    the first direct-login attempt.
    """
    from sqlalchemy import make_url

    # Pass the URL object directly — str() masks the password as '***' which
    # would be passed literally to asyncpg, causing InvalidPasswordError.
    app_url = make_url(asyncpg_url).set(username=_APP_ROLE, password=_APP_ROLE_PASSWORD)
    engine = create_async_engine(
        app_url, echo=False, pool_pre_ping=False, connect_args=_SEARCH_PATH
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session  # type: ignore[misc]
            await session.rollback()
    finally:
        await engine.dispose()
