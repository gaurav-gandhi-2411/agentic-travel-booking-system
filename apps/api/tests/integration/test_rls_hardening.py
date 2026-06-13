"""Integration tests for RLS hardening — bootstrap-auth resolver (no superuser/BYPASSRLS).

Three test classes:
  - TestBootstrapAuthResolver: the SECURITY INVOKER ``resolve_api_key_secure`` resolves a
    valid key to its tenant, and returns None for an invalid key, when called as the
    DIRECT-LOGIN non-superuser ``app_role`` under FORCE RLS — i.e. with no superuser and
    no BYPASSRLS anywhere. This is the production-equivalent posture.
  - TestBootstrapPolicyExposure: the ``api_keys_bootstrap_auth`` policy reveals exactly
    one row — the one whose unique key_hash the caller presents — and nothing else. No
    enumeration, no cross-tenant visibility.
  - TestForceRlsTableOwner: FORCE RLS applies to the table-owner role (non-superuser).

These tests use ``app_role_session`` (direct-login non-superuser), NOT a superuser
connection that does SET ROLE. A superuser-origin session bypasses RLS regardless of the
policy, which is exactly what masked the resolver bug on Cloud SQL. All tests run against
a real Postgres 16 container (see conftest.py); the live free-Postgres run uses the same
assertions via scripts/verify_resolver_free_pg.py.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from travel_agent.persistence.schema import DB_SCHEMA
from travel_agent.tenancy.service import (
    create_tenant_with_key,
    generate_raw_key,
    hash_key,
    resolve_key,
)

# ===========================================================================
# TestBootstrapAuthResolver
# ===========================================================================


class TestBootstrapAuthResolver:
    """resolve_api_key_secure works for a direct-login non-superuser under FORCE RLS.

    The SECURITY INVOKER function sets app.bootstrap_key_hash and reads the one row the
    additive bootstrap-auth policy permits — no superuser, no BYPASSRLS, no SECURITY
    DEFINER owner bypass.
    """

    async def test_resolver_returns_tenant_for_valid_key_as_app_role(
        self, async_session: AsyncSession, app_role_session: AsyncSession
    ) -> None:
        """A valid key resolves to its tenant via the non-superuser app_role session."""
        raw = generate_raw_key()
        tenant, _ = await create_tenant_with_key(
            async_session, name="Bootstrap Valid", slug="bootstrap-valid", raw_key=raw
        )
        await async_session.commit()

        # app_role_session connects DIRECTLY as the non-superuser app_role — FORCE RLS
        # binds it. With no app.current_tenant set, the only legitimate read is via the
        # bootstrap-auth policy, exercised inside resolve_api_key_secure.
        result: uuid.UUID | None = await app_role_session.scalar(
            text("SELECT resolve_api_key_secure(:kh)"),
            {"kh": hash_key(raw)},
        )
        assert result == tenant.id

    async def test_resolver_returns_none_for_unknown_key_as_app_role(
        self, app_role_session: AsyncSession
    ) -> None:
        """An unknown key resolves to NULL via the non-superuser app_role session."""
        result = await app_role_session.scalar(
            text("SELECT resolve_api_key_secure(:kh)"),
            {"kh": hash_key(generate_raw_key())},
        )
        assert result is None

    async def test_resolve_key_two_step_works_via_app_role(
        self, async_session: AsyncSession, app_role_session: AsyncSession
    ) -> None:
        """Full resolve_key() two-step returns the correct Tenant via the app role."""
        raw = generate_raw_key()
        tenant, _ = await create_tenant_with_key(
            async_session, name="Bootstrap TwoStep", slug="bootstrap-twostep", raw_key=raw
        )
        await async_session.commit()

        resolved = await resolve_key(raw, app_role_session)
        assert resolved is not None
        assert resolved.id == tenant.id
        assert resolved.slug == "bootstrap-twostep"

    async def test_bootstrap_guc_is_cleared_after_resolve(
        self, async_session: AsyncSession, app_role_session: AsyncSession
    ) -> None:
        """resolve_api_key_secure clears app.bootstrap_key_hash before returning."""
        raw = generate_raw_key()
        await create_tenant_with_key(
            async_session, name="Bootstrap Clear", slug="bootstrap-clear", raw_key=raw
        )
        await async_session.commit()

        async with app_role_session.begin():
            await app_role_session.scalar(
                text("SELECT resolve_api_key_secure(:kh)"), {"kh": hash_key(raw)}
            )
            leftover = await app_role_session.scalar(
                text("SELECT current_setting('app.bootstrap_key_hash', true)")
            )
            assert leftover in (None, ""), (
                f"bootstrap GUC leaked after resolve: {leftover!r}"
            )


# ===========================================================================
# TestBootstrapPolicyExposure
# ===========================================================================


class TestBootstrapPolicyExposure:
    """The bootstrap-auth policy reveals exactly the presented row, nothing more."""

    async def test_no_role_has_bypassrls_or_superuser(
        self, app_role_session: AsyncSession
    ) -> None:
        """app_role is neither superuser nor BYPASSRLS, and no resolver-owner role exists.

        The whole point of the redesign: the runtime path needs no privileged role.
        """
        flags = await app_role_session.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        )
        rolsuper, rolbypassrls = flags.one()
        assert rolsuper is False, "app_role must not be a superuser"
        assert rolbypassrls is False, "app_role must not have BYPASSRLS"

        # The dropped Cloud-SQL-era BYPASSRLS owner must not exist in this design.
        resolver_role = await app_role_session.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'dealhunter_resolver'")
        )
        assert resolver_role is None, (
            "dealhunter_resolver (BYPASSRLS) must not exist — the bootstrap-auth design "
            "needs no privileged owner role."
        )

    async def test_bootstrap_guc_reveals_only_the_presented_row(
        self, async_session: AsyncSession, app_role_session: AsyncSession
    ) -> None:
        """Setting app.bootstrap_key_hash to A's hash reveals A's row only — never B's.

        Proves the policy exposes a row IFF you present its exact unique hash: equality on
        a high-entropy unique column, no enumeration, no cross-tenant scan.
        """
        raw_a, raw_b = generate_raw_key(), generate_raw_key()
        tenant_a, _ = await create_tenant_with_key(
            async_session, name="Expose A", slug="expose-a", raw_key=raw_a
        )
        tenant_b, _ = await create_tenant_with_key(
            async_session, name="Expose B", slug="expose-b", raw_key=raw_b
        )
        await async_session.commit()

        async with app_role_session.begin():
            # No bootstrap hash set yet, no tenant context → default-deny, zero rows.
            none_visible = await app_role_session.scalar(
                text("SELECT COUNT(*) FROM api_keys")
            )
            assert none_visible == 0, (
                f"FORCE RLS default-deny failed: saw {none_visible} rows with no context"
            )

            # Present A's hash via the bootstrap GUC (parameterized set_config).
            await app_role_session.execute(
                text("SELECT set_config('app.bootstrap_key_hash', :kh, true)"),
                {"kh": hash_key(raw_a)},
            )

            # Exactly A's row is visible; B's row is not — even though we never set any
            # tenant context. The policy keys solely off the presented unique hash.
            a_visible = await app_role_session.scalar(
                text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
                {"tid": tenant_a.id},
            )
            b_visible = await app_role_session.scalar(
                text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
                {"tid": tenant_b.id},
            )
            total_visible = await app_role_session.scalar(
                text("SELECT COUNT(*) FROM api_keys")
            )
            assert a_visible == 1, f"Expected A's row visible, got {a_visible}"
            assert b_visible == 0, (
                f"Bootstrap exposure leaked: tenant B's row visible ({b_visible}) when "
                "presenting A's hash"
            )
            assert total_visible == 1, (
                f"Bootstrap policy exposed {total_visible} rows; must be exactly 1 "
                "(the presented hash). Any larger count means it is enumerable."
            )

    async def test_bootstrap_guc_unset_adds_no_visibility(
        self, async_session: AsyncSession, app_role_session: AsyncSession
    ) -> None:
        """With the bootstrap GUC unset, normal tenant-scoped SELECT is unchanged.

        Confirms the policy contributes zero added visibility during normal traffic: a
        tenant set only via app.current_tenant sees its own row and no other.
        """
        raw_a, raw_b = generate_raw_key(), generate_raw_key()
        tenant_a, _ = await create_tenant_with_key(
            async_session, name="Normal A", slug="normal-a", raw_key=raw_a
        )
        tenant_b, _ = await create_tenant_with_key(
            async_session, name="Normal B", slug="normal-b", raw_key=raw_b
        )
        await async_session.commit()

        async with app_role_session.begin():
            # Normal traffic: only app.current_tenant set, bootstrap GUC never touched.
            await app_role_session.execute(
                text(f"SET LOCAL app.current_tenant = '{tenant_a.id}'")
            )
            a_visible = await app_role_session.scalar(
                text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
                {"tid": tenant_a.id},
            )
            b_visible = await app_role_session.scalar(
                text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
                {"tid": tenant_b.id},
            )
            assert a_visible == 1, f"Tenant A should see its own row, got {a_visible}"
            assert b_visible == 0, (
                f"Tenant isolation broken: A saw {b_visible} of B's rows during normal "
                "traffic (bootstrap GUC unset)."
            )


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
        # Objects live in the dedicated schema; the owner role needs USAGE on it
        # (no longer implicit as it was under public).
        await async_session.execute(text(f"GRANT USAGE ON SCHEMA {DB_SCHEMA} TO app_owner"))
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
