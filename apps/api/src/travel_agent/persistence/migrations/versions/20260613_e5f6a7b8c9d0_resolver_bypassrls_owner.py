"""Dedicated NOLOGIN BYPASSRLS owner for resolve_api_key_secure.

Root cause (surfaced on Cloud SQL): the bootstrap auth resolver
``resolve_api_key_secure`` is SECURITY DEFINER, so it executes as its OWNER.
Migration b2c3d4e5f6a7 created it owned by whoever ran the migration. On Cloud SQL
the migration-runner is ``postgres`` — a member of ``cloudsqlsuperuser`` but NOT a
real superuser and with ``rolbypassrls = false``. Under FORCE ROW LEVEL SECURITY,
that owner is itself bound by RLS, so the resolver's bootstrap SELECT (which runs
with no ``app.current_tenant`` context) returns 0 rows and EVERY API key fails to
resolve — production auth is dead. (In the testcontainers suite the function happened
to work only because the container's migration-runner is a real superuser.)

Fix (isolation-preserving): give the resolver a dedicated, least-privilege owner —
a NOLOGIN BYPASSRLS role ``dealhunter_resolver``. SECURITY DEFINER then runs as this
role, so the single narrow bootstrap lookup bypasses FORCE RLS, while:
  - FORCE ROW LEVEL SECURITY stays ON for both tables (unchanged).
  - The application role (``dealhunter_app``) stays non-superuser, non-bypassrls and
    fully policed. It is NOT a member of ``dealhunter_resolver`` and cannot SET ROLE
    to it, so it can never escalate out of its tenant scope.
  - ``dealhunter_resolver`` is NOLOGIN (cannot open a connection / serve traffic) and
    is granted only SELECT on the two tables the resolver reads. Minimum bypass surface.

The migration-runner is granted membership in the new role so it can reassign
ownership here and manage the function in future migrations. The runner is always an
admin role (a real superuser in tests; cloudsqlsuperuser on Cloud SQL) — never a
traffic-serving role — so this membership is isolation-neutral.

Verified live on dealhunter-prod-pg16 (Cloud SQL PG16): a valid key resolves to its
tenant, cross-tenant SELECT returns 0 rows, and no-context SELECT returns 0 rows for
the application role.

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Dedicated NOLOGIN BYPASSRLS owner role (idempotent — roles are cluster-global).
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dealhunter_resolver') THEN
            CREATE ROLE dealhunter_resolver NOLOGIN BYPASSRLS;
          END IF;
        END $$
        """
    )
    # 2. The migration-runner must be able to SET ROLE to the new owner to reassign
    #    ownership (and to manage the function in future migrations). Kept, not revoked:
    #    the runner is an admin role, never a traffic-serving role.
    op.execute("GRANT dealhunter_resolver TO CURRENT_USER")
    # 3. ALTER ... OWNER TO requires the new owner to hold CREATE on the function's
    #    schema. Grant transiently and revoke immediately after the reassignment.
    op.execute("GRANT CREATE ON SCHEMA public TO dealhunter_resolver")
    op.execute("ALTER FUNCTION resolve_api_key_secure(text) OWNER TO dealhunter_resolver")
    op.execute("REVOKE CREATE ON SCHEMA public FROM dealhunter_resolver")
    # 4. SECURITY DEFINER bypasses RLS but NOT table-level GRANTs — the owner needs
    #    SELECT on exactly the two tables the resolver reads. Read-only, least privilege.
    op.execute("GRANT SELECT ON tenants, api_keys TO dealhunter_resolver")


def downgrade() -> None:
    # Return the function to the migration-runner and drop the dedicated owner.
    op.execute("REVOKE SELECT ON tenants, api_keys FROM dealhunter_resolver")
    op.execute("ALTER FUNCTION resolve_api_key_secure(text) OWNER TO CURRENT_USER")
    op.execute("DROP ROLE IF EXISTS dealhunter_resolver")
