from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

RLS_TENANT_VAR = "app.current_tenant"


async def apply_rls_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Apply the RLS tenant context for the current transaction.

    Must be called after beginning a transaction and before any tenant-scoped
    query. The SET LOCAL is scoped to the current transaction only.

    Note: asyncpg does not support parameterized SET LOCAL statements
    (syntax error at or near "$1"), so the UUID is inlined after validation.
    The tenant_id must be a valid UUID string — callers should pass str(uuid).
    """
    import uuid as _uuid  # noqa: PLC0415

    # Validate the value is a real UUID before inlining it into the SQL string.
    # This is safe — UUID.__str__ only emits hex digits and hyphens.
    validated = str(_uuid.UUID(tenant_id))
    await session.execute(text(f"SET LOCAL {RLS_TENANT_VAR} = '{validated}'"))


def rls_policy_sql(table: str, policy_name: str) -> str:
    """Return the SQL to create a SELECT RLS policy scoped to app.current_tenant."""
    return (
        f"CREATE POLICY {policy_name} ON {table} "
        f"FOR SELECT USING (tenant_id = current_setting('{RLS_TENANT_VAR}')::uuid)"
    )
