from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import models so autogenerate detects all tables.
# tenancy/models.py defines Base; all model classes must import Base from there.
from travel_agent.persistence.schema import DB_SCHEMA
from travel_agent.tenancy.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        msg = "DATABASE_URL must be set to run migrations"
        raise RuntimeError(msg)
    for prefix in ("postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+asyncpg://" + raw[len(prefix):]
    return raw


def run_migrations_offline() -> None:
    # Emit the schema bootstrap so `alembic upgrade --sql` is also schema-scoped.
    # version_table_schema keeps our migration history in DB_SCHEMA, never `public`.
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=DB_SCHEMA,
    )
    context.execute(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"')
    context.execute(f'SET search_path TO "{DB_SCHEMA}"')
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    # Blast-radius control for a SHARED instance: search_path is pinned to DB_SCHEMA via
    # the engine's startup parameter (server_settings) and the schema is created+committed
    # BEFORE this runs (see _run_async_migrations). Every unqualified CREATE/ALTER/POLICY
    # then resolves to DB_SCHEMA and CANNOT touch public; version_table_schema isolates
    # alembic_version into DB_SCHEMA so histories never collide across projects.
    #
    # Do NOT issue any statement here before context.begin_transaction(): that would open a
    # transaction alembic does not own, and the connection's close would roll the entire
    # migration back (observed: the schema silently vanished after a "successful" run).
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=DB_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Apply the schema as a connection STARTUP parameter, not only via SET. This is
        # robust even if a connection pooler hands out a fresh server connection between
        # statements: the startup search_path re-applies per physical connection, so the
        # migration's unqualified DDL always lands in DB_SCHEMA, never public. (We still
        # run on the session-mode pooler / a direct connection; this is belt-and-suspenders.)
        connect_args={"server_settings": {"search_path": DB_SCHEMA}},
    )
    # Create the dedicated schema in its OWN committed transaction first, so it exists
    # before alembic creates its version table (version_table_schema=DB_SCHEMA) and before
    # any DDL resolves through search_path. CREATE SCHEMA IF NOT EXISTS is idempotent.
    async with connectable.begin() as conn:
        await conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"')
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
