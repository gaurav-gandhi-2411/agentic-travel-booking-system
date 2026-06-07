from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _normalise_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver scheme."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
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


async def set_rls_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Set the per-request Postgres session variable consumed by RLS policies.

    asyncpg rejects parameterized SET LOCAL (syntax error at '$1'), so the UUID
    is validated then inlined. uuid.UUID() ensures only hex+hyphens are emitted.
    """
    validated = str(uuid.UUID(tenant_id))
    await session.execute(text(f"SET LOCAL app.current_tenant = '{validated}'"))
