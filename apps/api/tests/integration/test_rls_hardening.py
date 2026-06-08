"""Integration tests for RLS hardening (Phase 3.2-A.1 Step 4).

Two test classes:
  - TestSecurityDefinerBootstrap: SECURITY DEFINER function works via non-privileged
    app_role with no tenant context (required for the bootstrap lookup step).
  - TestForceRlsTableOwner: FORCE RLS applies to the table-owner role, not only to
    non-owner roles. Table-owner connections (non-superuser) see zero rows from other
    tenants even when they own the table.

All tests run against a real Postgres 16 container (see conftest.py).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from travel_agent.tenancy.service import (
    create_tenant_with_key,
    generate_raw_key,
    hash_key,
    resolve_key,
)

# ===========================================================================
# TestSecurityDefinerBootstrap
# ===========================================================================


class TestSecurityDefinerBootstrap:
    """SECURITY DEFINER bootstrap: resolve_api_key_secure() bypasses FORCE RLS
    for non-privileged callers so the tenant UUID can be obtained before any
    RLS context exists.
    """

    async def test_sd_fn_bypasses_rls_for_non_privileged_caller(
        self, async_session: AsyncSession, rls_session: AsyncSession
    ) -> None:
        """resolve_api_key_secure() returns tenant UUID for app_role with no context."""
        raw = generate_raw_key()
        tenant, _ = await create_tenant_with_key(
            async_session, name="SD Bootstrap", slug="sd-bootstrap", raw_key=raw
        )
        await async_session.commit()

        # rls_session is the non-privileged app_role — no app.current_tenant set.
        # The SECURITY DEFINER function must bypass FORCE RLS on its owner's behalf.
        result: uuid.UUID | None = await rls_session.scalar(
            text("SELECT resolve_api_key_secure(:kh)"),
            {"kh": hash_key(raw)},
        )
        assert result == tenant.id

    async def test_sd_fn_returns_none_for_unknown_key(
        self, rls_session: AsyncSession
    ) -> None:
        """resolve_api_key_secure() returns NULL for a key that doesn't exist."""
        result = await rls_session.scalar(
            text("SELECT resolve_api_key_secure(:kh)"),
            {"kh": hash_key(generate_raw_key())},
        )
        assert result is None

    async def test_resolve_key_two_step_works_via_rls_session(
        self, async_session: AsyncSession, rls_session: AsyncSession
    ) -> None:
        """Full resolve_key() two-step returns correct Tenant via rls_session."""
        raw = generate_raw_key()
        tenant, _ = await create_tenant_with_key(
            async_session, name="SD TwoStep", slug="sd-twostep", raw_key=raw
        )
        await async_session.commit()

        resolved = await resolve_key(raw, rls_session)
        assert resolved is not None
        assert resolved.id == tenant.id
        assert resolved.slug == "sd-twostep"


# ===========================================================================
# TestForceRlsTableOwner
# ===========================================================================


class TestForceRlsTableOwner:
    """FORCE RLS applies to the table-owner role (non-superuser).

    Postgres's FORCE ROW LEVEL SECURITY means even the table owner — when not a
    superuser — is subject to RLS policies. This test verifies that claim by:
    1. Creating an `app_owner` non-superuser role and transferring table ownership to it.
    2. Switching the connection to `app_owner` via SET LOCAL ROLE.
    3. Asserting that cross-tenant rows are invisible (b_count == 0).
    4. Restoring original ownership in a finally block so other tests are unaffected.
    """

    async def test_force_rls_applies_to_table_owner_role(
        self, async_session: AsyncSession
    ) -> None:
        """FORCE RLS: table-owner connection sees only its own tenant's rows."""
        # ── seed data as superuser ────────────────────────────────────────────
        raw_a, raw_b = generate_raw_key(), generate_raw_key()
        tenant_a, _ = await create_tenant_with_key(
            async_session, name="FRLS Owner A", slug="frls-owner-a", raw_key=raw_a
        )
        tenant_b, _ = await create_tenant_with_key(
            async_session, name="FRLS Owner B", slug="frls-owner-b", raw_key=raw_b
        )
        await async_session.commit()

        # ── set up app_owner role and transfer ownership ──────────────────────
        await async_session.execute(
            text(
                "DO $$ BEGIN "
                "  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_owner') "
                "  THEN CREATE ROLE app_owner NOLOGIN; "
                "  END IF; "
                "END $$"
            )
        )
        await async_session.execute(text("GRANT app_owner TO CURRENT_USER"))
        await async_session.execute(text("ALTER TABLE tenants OWNER TO app_owner"))
        await async_session.execute(text("ALTER TABLE api_keys OWNER TO app_owner"))
        # app_owner needs privileges to query its own tables
        await async_session.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, api_keys TO app_owner")
        )
        # resolve_api_key_secure needs EXECUTE for app_owner
        await async_session.execute(
            text("GRANT EXECUTE ON FUNCTION resolve_api_key_secure(text) TO app_owner")
        )
        await async_session.commit()

        # ── FORCE RLS isolation test as table owner ───────────────────────────
        try:
            async with async_session.begin():
                # Switch to app_owner — now subject to FORCE ROW LEVEL SECURITY
                await async_session.execute(text("SET LOCAL ROLE app_owner"))

                # No tenant context: FORCE RLS → default-deny, zero rows
                no_ctx_count = await async_session.scalar(
                    text("SELECT COUNT(*) FROM api_keys")
                )
                assert no_ctx_count == 0, (
                    f"FORCE RLS failed: table owner saw {no_ctx_count} rows with no context"
                )

                # Set RLS context to tenant A (inline validated UUID)
                tid_a = str(tenant_a.id)
                await async_session.execute(
                    text(f"SET LOCAL app.current_tenant = '{tid_a}'")
                )

                # Tenant A's own key is visible
                a_count = await async_session.scalar(
                    text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
                    {"tid": tenant_a.id},
                )
                assert a_count == 1, (
                    f"Expected 1 row for tenant A under FORCE RLS, got {a_count}"
                )

                # Tenant B's key is NOT visible to A's context
                b_count = await async_session.scalar(
                    text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
                    {"tid": tenant_b.id},
                )
                assert b_count == 0, (
                    f"FORCE RLS isolation FAILED: table owner saw {b_count} of tenant B's rows"
                )
        finally:
            # ── restore ownership so other tests aren't affected ──────────────
            await async_session.execute(
                text("ALTER TABLE tenants OWNER TO CURRENT_USER")
            )
            await async_session.execute(
                text("ALTER TABLE api_keys OWNER TO CURRENT_USER")
            )
            await async_session.commit()
