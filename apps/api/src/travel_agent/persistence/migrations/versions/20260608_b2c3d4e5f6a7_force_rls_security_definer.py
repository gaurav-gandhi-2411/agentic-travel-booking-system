"""FORCE ROW LEVEL SECURITY + resolve_api_key_secure SECURITY DEFINER function

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-08

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── FORCE Row Level Security ──────────────────────────────────────────────
    # ENABLE (from migration a1b2c3d4e5f6) exempts table owners.
    # FORCE extends the policy to the table owner role as well.
    # Superusers still bypass RLS by Postgres design; the SECURITY DEFINER
    # function below provides the legitimate bypass for the auth bootstrap path.
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY")

    # ── SECURITY DEFINER bootstrap function ───────────────────────────────────
    # Returns only the tenant UUID — minimum data needed for the bootstrap lookup.
    # SECURITY DEFINER runs as the function owner (the superuser/BYPASSRLS role
    # that created it), bypassing FORCE RLS for this one narrow operation.
    # SET search_path pins the dedicated schema (prevents search-path injection AND
    # keeps the lookup inside DealHunter's isolated schema on a shared instance).
    op.execute(
        """
        CREATE FUNCTION resolve_api_key_secure(p_key_hash text)
        RETURNS uuid
        SECURITY DEFINER
        SET search_path = dealhunter
        LANGUAGE sql
        AS $$
            SELECT t.id
            FROM   api_keys k
            JOIN   tenants  t ON t.id = k.tenant_id
            WHERE  k.key_hash  = p_key_hash
              AND  k.is_active = true
              AND  t.is_active = true
            LIMIT 1;
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS resolve_api_key_secure(text)")
    op.execute("ALTER TABLE api_keys NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
