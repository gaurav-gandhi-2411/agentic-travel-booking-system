"""Tighten RLS INSERT WITH CHECK: allow bootstrap INSERT (no tenant context) while
blocking cross-tenant INSERT from a tenant-scoped session.

The FOR ALL policies from migration a1b2c3d4e5f6 had only a USING clause.
PostgreSQL defaults WITH CHECK to the same expression as USING when unspecified.
This blocks INSERT when app.current_tenant is unset (the seed/bootstrap path),
because the check evaluates to NEW.id = NULL which is false.

Tightened WITH CHECK logic:
  - If no tenant context is set (bootstrap/seed/provisioning): allow INSERT.
  - If a tenant context IS set: the new row must belong to that tenant.

This closes the cross-tenant INSERT hole that WITH CHECK (true) would have left
open: a session scoped to tenant A cannot INSERT a row stamped with tenant B's ID.

SELECT/UPDATE/DELETE isolation (USING clause) is unchanged.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER POLICY tenants_rls_isolation ON tenants
        USING (id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (
            NULLIF(current_setting('app.current_tenant', true), '') IS NULL
            OR id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        ALTER POLICY api_keys_rls_isolation ON api_keys
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (
            NULLIF(current_setting('app.current_tenant', true), '') IS NULL
            OR tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    # Revert to USING-only (no explicit WITH CHECK — PostgreSQL defaults it back
    # to the USING expression, which blocks bootstrap INSERT).
    op.execute(
        """
        ALTER POLICY tenants_rls_isolation ON tenants
        USING (id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute(
        """
        ALTER POLICY api_keys_rls_isolation ON api_keys
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
