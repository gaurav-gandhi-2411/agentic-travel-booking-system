from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

from travel_agent.persistence.rls import _validate_tenant_id
from travel_agent.persistence.schema import DB_SCHEMA

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _normalise_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver scheme."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def get_engine() -> AsyncEngine:
    """Return the singleton AsyncEngine, creating it on first call."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        raw = os.environ.get("DATABASE_URL")
        if not raw:
            msg = "DATABASE_URL environment variable is not set"
            raise RuntimeError(msg)
        _engine = create_async_engine(
            _normalise_url(raw),
            echo=os.environ.get("DB_ECHO", "false").lower() == "true",
            pool_pre_ping=True,
            # Pin every pooled connection to DealHunter's dedicated schema. The ORM and
            # the resolver call resolve to DB_SCHEMA objects only; `public` is never on
            # the path, so a shared instance's `public`/co-tenant objects are unreachable.
            connect_args={"server_settings": {"search_path": DB_SCHEMA}},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton session factory, creating it on first call."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an AsyncSession."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def assert_runtime_role_unprivileged(session: AsyncSession) -> None:
    """Refuse to start if the connected DB role can bypass Row-Level Security.

    Tenant isolation depends on FORCE ROW LEVEL SECURITY binding the connection. A role
    with ``rolsuper`` or ``rolbypassrls`` skips RLS entirely and would silently void all
    cross-tenant isolation — with no error and no log. On managed Postgres (e.g. Supabase)
    the platform admin role ``postgres`` has ``rolbypassrls = true``; it is for migrations
    and provisioning only. The deployed app MUST connect as a dedicated least-privilege
    role (e.g. ``dealhunter_app``) that is non-superuser and non-BYPASSRLS.

    This guard makes "never serve traffic as a bypass role" structural, not a deploy-
    checklist hope: it raises RuntimeError (hard fail, loud) at startup otherwise.
    """
    row = (
        await session.execute(
            text(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()
    role_name, is_super, is_bypass = str(row[0]), bool(row[1]), bool(row[2])
    if is_super or is_bypass:
        msg = (
            f"REFUSING TO START: the database role '{role_name}' can BYPASS Row-Level "
            f"Security (rolsuper={is_super}, rolbypassrls={is_bypass}). Serving traffic as "
            f"this role would SILENTLY VOID all tenant isolation. Point DATABASE_URL at a "
            f"dedicated least-privilege application role (non-superuser, non-BYPASSRLS) — "
            f"never the platform admin/superuser role."
        )
        raise RuntimeError(msg)


async def set_rls_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Set the per-request Postgres session variable consumed by RLS policies.

    asyncpg rejects parameterized SET LOCAL (syntax error at '$1'), so the UUID
    is validated then inlined. Raises ValueError if tenant_id is not a valid UUID.
    """
    validated = _validate_tenant_id(tenant_id)
    await session.execute(text(f"SET LOCAL app.current_tenant = '{validated}'"))
