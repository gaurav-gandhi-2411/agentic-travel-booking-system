from __future__ import annotations

import uuid as _uuid_mod

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

RLS_TENANT_VAR = "app.current_tenant"


def _validate_tenant_id(tenant_id: str) -> str:
    """Return the canonical UUID string, or raise ValueError for invalid input.

    Every SET LOCAL site must call this before constructing SQL. uuid.UUID()
    accepts only the canonical 8-4-4-4-12 hex+hyphen form — any injection-shaped
    value raises ValueError before reaching the database.
    """
    return str(_uuid_mod.UUID(tenant_id))


async def apply_rls_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Apply the RLS tenant context for the current transaction.

    Must be called after beginning a transaction and before any tenant-scoped
    query. The SET LOCAL is scoped to the current transaction only.

    Raises ValueError if tenant_id is not a valid UUID string.
    """
    validated = _validate_tenant_id(tenant_id)
    await session.execute(text(f"SET LOCAL {RLS_TENANT_VAR} = '{validated}'"))


def rls_policy_sql(table: str, policy_name: str) -> str:
    """Return the SQL to create a SELECT RLS policy scoped to app.current_tenant."""
    return (
        f"CREATE POLICY {policy_name} ON {table} "
        f"FOR SELECT USING (tenant_id = current_setting('{RLS_TENANT_VAR}')::uuid)"
    )
