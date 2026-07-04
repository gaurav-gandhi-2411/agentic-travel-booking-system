from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import structlog
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

from travel_agent.persistence.rls import _validate_tenant_id

_logger = structlog.get_logger(__name__)

# Supabase pooler ports (ADR-0028) -- logged at engine construction so a canary
# smoke test can PROVE which pooler is actually active from structured logs,
# rather than inferring it from the absence of a pool-exhaustion error (which
# could also mean the fallback-to-session-pooler path silently activated under
# light load that never would have triggered EMAXCONNSESSION either way).
_SESSION_POOLER_PORT = 5432
_TRANSACTION_POOLER_PORT = 6543
_POOLER_MODE_BY_PORT: dict[int, str] = {
    _SESSION_POOLER_PORT: "session",
    _TRANSACTION_POOLER_PORT: "transaction",
}

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


# Runtime pool sizing (ADR-0028) — explicit, not SQLAlchemy defaults. The defaults
# (pool_size=5, max_overflow=10 => 15) were the bug: they exactly matched the
# Supabase session pooler's own 15-client ceiling, so a single Cloud Run instance
# alone could exhaust the ENTIRE shared-with-a-co-tenant pooler under a burst.
#
# The runtime engine now connects over the Supabase TRANSACTION pooler (port 6543)
# instead, whose free-tier ceiling is 200 max client connections (Supabase compute-
# and-disk docs, Nano/free tier) -- a different, much larger number from the
# session-mode "pool_size: 15" that appeared in the EMAXCONNSESSION error, because
# transaction mode multiplexes many client connections over few backend ones
# instead of dedicating one backend connection per client for its lifetime.
#
# Math: budget ourselves HALF the ceiling, leaving the other half for the
# co-tenant project sharing this Supabase instance:
#   200 (free-tier Supavisor client ceiling)
#   / 2  (deliberate 50/50 split with the co-tenant -- their usage is unknown/
#         uncontracted, so we don't claim more than half by default)
#   = 100 connections is OUR worst-case budget
#   / 20 (Cloud Run --max-instances, deploy-prod.yml -- each instance gets its own
#         engine/pool, so the FLEET-WIDE worst case is max_instances x per-instance)
#   = 5 connections per instance, at the theoretical full-scale-out worst case.
_POOL_SIZE = 3  # always-open connections per instance
_MAX_OVERFLOW = 2  # burst connections beyond pool_size, per instance
# 3 + 2 = 5/instance x 20 max instances = 100 worst-case fleet total = 50% of the
# 200-client ceiling, leaving 100 for the co-tenant.

# Fails fast into TenantAuthMiddleware's retry-then-503 layer (auth.py) rather than
# blocking a request for up to SQLAlchemy's 30s default while waiting on a full pool.
_POOL_TIMEOUT_SECONDS = 5


def _normalise_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver scheme."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def get_engine() -> AsyncEngine:
    """Return the singleton AsyncEngine, creating it on first call.

    Prefers DATABASE_URL_RUNTIME (the Supabase transaction pooler, port 6543) for
    the app's own query traffic; falls back to DATABASE_URL (session pooler) if
    the runtime-specific secret isn't provisioned, so this is safe to deploy before
    or after DATABASE_URL_RUNTIME exists. Alembic migrations always use DATABASE_URL
    directly (env.py) -- migrations stay on the session pooler unconditionally.
    """
    global _engine  # noqa: PLW0603
    if _engine is None:
        source = (
            "DATABASE_URL_RUNTIME" if os.environ.get("DATABASE_URL_RUNTIME") else "DATABASE_URL"
        )
        raw = os.environ.get("DATABASE_URL_RUNTIME") or os.environ.get("DATABASE_URL")
        if not raw:
            msg = "DATABASE_URL_RUNTIME or DATABASE_URL environment variable must be set"
            raise RuntimeError(msg)
        _engine = create_async_engine(
            _normalise_url(raw),
            echo=os.environ.get("DB_ECHO", "false").lower() == "true",
            pool_pre_ping=True,
            pool_size=_POOL_SIZE,
            max_overflow=_MAX_OVERFLOW,
            pool_timeout=_POOL_TIMEOUT_SECONDS,
            # NOTE: no search_path server_setting here. Tenant/ApiKey now declare
            # schema=DB_SCHEMA explicitly (tenancy/models.py) and
            # resolve_api_key_secure has SET search_path bound into the function
            # definition itself (migration b2c3d4e5f6a7) -- neither depends on the
            # connection's ambient search_path, which is what makes this path safe
            # under transaction pooling (a pooled connection can be handed to a
            # different logical session between transactions, so a search_path
            # pinned only at connection-open time isn't guaranteed to still apply).
        )
        # Port only -- never log host/credentials. This is the one log line that
        # PROVES which pooler is actually active: the absence of a pool-exhaustion
        # error is NOT proof (the session-pooler fallback could be silently active
        # under load too light to ever hit its 15-client ceiling). A canary smoke
        # test should grep for this line, not just check for a lack of 500s.
        port = make_url(_normalise_url(raw)).port
        pooler_mode = _POOLER_MODE_BY_PORT.get(port, "unknown") if port is not None else "unknown"
        _logger.info(
            "db_engine_configured",
            port=port,
            pooler_mode=pooler_mode,
            source_env_var=source,
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
