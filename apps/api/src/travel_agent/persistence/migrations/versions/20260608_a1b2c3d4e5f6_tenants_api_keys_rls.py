"""tenants + api_keys tables with Postgres RLS policies

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-06-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── tenants ──────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column(
            "inventory_adapter",
            sa.String(50),
            nullable=False,
            server_default="aviasales",
        ),
        sa.Column(
            "affiliate_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "rate_limit_tier",
            sa.String(50),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])

    # ── api_keys ─────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_prefix", sa.String(8), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_api_keys_tenant_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    # ── Row Level Security ────────────────────────────────────────────────────
    # ENABLE (without FORCE) means superusers/table-owner bypass RLS — required
    # so the auth-bootstrap query (key→tenant resolution in Step 3) works before
    # any tenant context is set on the connection. FORCE RLS + SECURITY DEFINER
    # function is a follow-on hardening task (post-3.2-A).
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")

    # NULLIF guards the empty-string case: current_setting returns '' (not NULL)
    # when the variable is unset and missing_ok=true. Casting '' to UUID raises,
    # so we coerce it to NULL first — result: no rows visible when no context set.
    op.execute(
        """
        CREATE POLICY tenants_rls_isolation ON tenants
        FOR ALL
        USING (
            id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY api_keys_rls_isolation ON api_keys
        FOR ALL
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS api_keys_rls_isolation ON api_keys")
    op.execute("DROP POLICY IF EXISTS tenants_rls_isolation ON tenants")
    op.execute("ALTER TABLE api_keys DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_tenant_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
