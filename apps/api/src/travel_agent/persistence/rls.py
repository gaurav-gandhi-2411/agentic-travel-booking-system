from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

RLS_TENANT_VAR = "app.current_tenant"


async def apply_rls_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Apply the RLS tenant context for the current transaction.

    Must be called after beginning a transaction and before any tenant-scoped
    query. The SET LOCAL is scoped to the current transaction only.
    """
    await session.execute(
        text(f"SET LOCAL {RLS_TENANT_VAR} = :tid"),
        {"tid": tenant_id},
    )


def rls_policy_sql(table: str, policy_name: str) -> str:
    """Return the SQL to create a SELECT RLS policy scoped to app.current_tenant."""
    return (
        f"CREATE POLICY {policy_name} ON {table} "
        f"FOR SELECT USING (tenant_id = current_setting('{RLS_TENANT_VAR}')::uuid)"
    )
