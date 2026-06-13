"""Integration tests for tenancy: demo-key backward compat, RLS isolation,
affiliate config, and RequestState population.

All tests run against a real Postgres 16 container (see conftest.py).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.sql import text

from travel_agent.persistence.rls import apply_rls_tenant
from travel_agent.tenancy.config import is_affiliate_enabled
from travel_agent.tenancy.models import ApiKey, Tenant
from travel_agent.tenancy.service import (
    create_tenant_with_key,
    generate_raw_key,
    resolve_key,
    seed_demo_tenant,
)

# ===========================================================================
# TestDemoKeyBackwardCompat
# ===========================================================================


class TestDemoKeyBackwardCompat:
    """DEMO_API_KEY continues to authenticate as the seeded demo tenant."""

    async def test_demo_key_authenticates(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEMO_API_KEY env var authenticates as demo tenant after seed."""
        raw_key = "test-demo-key-1234"
        monkeypatch.setenv("DEMO_API_KEY", raw_key)
        await seed_demo_tenant(async_session)
        tenant = await resolve_key(raw_key, async_session)
        assert tenant is not None
        assert tenant.slug == "demo"

    async def test_invalid_key_returns_none(self, async_session: AsyncSession) -> None:
        """A random key that was never created returns None."""
        tenant = await resolve_key("totally-fake-key-xyz", async_session)
        assert tenant is None

    async def test_wrong_key_returns_none(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A valid-format key that doesn't exist in the DB returns None."""
        # Seed a tenant so DB is non-empty
        monkeypatch.setenv("DEMO_API_KEY", "wrong-key-test-seed")
        await seed_demo_tenant(async_session)
        # Generate a fresh key that was never stored
        fresh = generate_raw_key()
        tenant = await resolve_key(fresh, async_session)
        assert tenant is None

    async def test_seed_is_idempotent(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling seed_demo_tenant twice does not raise or create duplicates."""
        monkeypatch.setenv("DEMO_API_KEY", "idempotent-key-test-xyz99")
        await seed_demo_tenant(async_session)
        # Second call should be a no-op (slug "demo" already exists)
        await seed_demo_tenant(async_session)
        # Count tenants with slug "demo" — must be exactly 1
        result = await async_session.execute(
            select(func.count()).select_from(Tenant).where(Tenant.slug == "demo")
        )
        count = result.scalar()
        assert count == 1, f"Expected 1 demo tenant, found {count}"


# ===========================================================================
# TestRLSIsolation
# ===========================================================================


class TestRLSIsolation:
    """Row Level Security: tenant A cannot read tenant B's api_keys.

    Important: Postgres superusers bypass RLS even with FORCE ROW LEVEL SECURITY.
    The testcontainers default user ('test') is a superuser. These tests use a
    dedicated 'app_role' non-superuser fixture (rls_session) so that RLS policies
    are actually enforced. Data is inserted via async_session (superuser), then
    queried via rls_session (non-superuser, subject to RLS).
    """

    async def test_tenant_a_cannot_read_tenant_b_rows(
        self, async_session: AsyncSession, rls_session: AsyncSession
    ) -> None:
        """Core RLS test: tenant A's session cannot read tenant B's api_keys row.

        Data is seeded as superuser (bypasses RLS for insert), then read back as
        app_role (non-superuser). apply_rls_tenant uses SET LOCAL which must be
        within an active transaction — we wrap queries in begin().
        """
        raw_a = generate_raw_key()
        raw_b = generate_raw_key()

        # Insert both tenants as superuser (bypasses RLS) then commit
        tenant_a, _key_a = await create_tenant_with_key(
            async_session, name="Tenant Alpha", slug="rls-tenant-a", raw_key=raw_a
        )
        tenant_b, _key_b = await create_tenant_with_key(
            async_session, name="Tenant Beta", slug="rls-tenant-b", raw_key=raw_b
        )
        await async_session.commit()

        # Query as non-superuser (app_role) inside a transaction where we SET LOCAL
        async with rls_session.begin():
            await rls_session.execute(text("SET LOCAL ROLE app_role"))
            await apply_rls_tenant(rls_session, str(tenant_a.id))

            # app_role with tenant_a context: can see tenant_a's own api_key
            result_a = await rls_session.execute(
                select(ApiKey).where(ApiKey.tenant_id == tenant_a.id)
            )
            own_key = result_a.scalars().first()
            assert own_key is not None, "Tenant A should see their own api_key row"

            # app_role with tenant_a context: CANNOT see tenant_b's api_key (RLS blocks)
            result_b = await rls_session.execute(
                select(ApiKey).where(ApiKey.tenant_id == tenant_b.id)
            )
            cross_key = result_b.scalars().first()
            assert cross_key is None, (
                f"RLS isolation failed: tenant A read tenant B's api_key row "
                f"(tenant_b.id={tenant_b.id})"
            )

    async def test_tenant_a_cannot_update_tenant_b_rows(
        self, async_session: AsyncSession, rls_session: AsyncSession
    ) -> None:
        """FOR UPDATE isolation: tenant A cannot UPDATE rows belonging to tenant B.

        Verifies that the per-command UPDATE policy's USING clause is enforced.
        The UPDATE targets tenant B's row by primary key; under FORCE RLS with
        tenant A's context, the row is invisible and rowcount must be 0.
        """
        raw_a, raw_b = generate_raw_key(), generate_raw_key()
        tenant_a, _ = await create_tenant_with_key(
            async_session, name="RLS Update A", slug="rls-upd-a", raw_key=raw_a
        )
        tenant_b, _ = await create_tenant_with_key(
            async_session, name="RLS Update B", slug="rls-upd-b", raw_key=raw_b
        )
        await async_session.commit()

        async with rls_session.begin():
            await rls_session.execute(text("SET LOCAL ROLE app_role"))
            await apply_rls_tenant(rls_session, str(tenant_a.id))

            result = await rls_session.execute(
                text("UPDATE tenants SET name = 'PWNED' WHERE id = :id").bindparams(
                    id=tenant_b.id
                )
            )
            assert result.rowcount == 0, (
                f"UPDATE isolation FAILED: tenant A updated {result.rowcount} of "
                "tenant B's rows. FOR UPDATE USING policy may not be applied."
            )

        name_after = await async_session.scalar(
            text("SELECT name FROM tenants WHERE id = :id").bindparams(id=tenant_b.id)
        )
        assert name_after == "RLS Update B", (
            f"Tenant B's name was mutated to '{name_after}' despite UPDATE isolation."
        )

    async def test_tenant_a_cannot_delete_tenant_b_rows(
        self, async_session: AsyncSession, rls_session: AsyncSession
    ) -> None:
        """FOR DELETE isolation: tenant A cannot DELETE rows belonging to tenant B.

        Verifies that the per-command DELETE policy's USING clause is enforced.
        The DELETE targets tenant B's row by primary key; under FORCE RLS with
        tenant A's context, the row is invisible and rowcount must be 0.
        """
        raw_a, raw_b = generate_raw_key(), generate_raw_key()
        tenant_a, _ = await create_tenant_with_key(
            async_session, name="RLS Delete A", slug="rls-del-a", raw_key=raw_a
        )
        tenant_b, _ = await create_tenant_with_key(
            async_session, name="RLS Delete B", slug="rls-del-b", raw_key=raw_b
        )
        await async_session.commit()

        async with rls_session.begin():
            await rls_session.execute(text("SET LOCAL ROLE app_role"))
            await apply_rls_tenant(rls_session, str(tenant_a.id))

            result = await rls_session.execute(
                text("DELETE FROM tenants WHERE id = :id").bindparams(id=tenant_b.id)
            )
            assert result.rowcount == 0, (
                f"DELETE isolation FAILED: tenant A deleted {result.rowcount} of "
                "tenant B's rows. FOR DELETE USING policy may not be applied."
            )

        b_count = await async_session.scalar(
            text("SELECT COUNT(*) FROM tenants WHERE id = :id").bindparams(id=tenant_b.id)
        )
        assert b_count == 1, (
            "Tenant B's row was deleted — DELETE isolation failed."
        )

    async def test_no_rls_context_sees_no_rows(
        self, async_session: AsyncSession, rls_session: AsyncSession
    ) -> None:
        """With no tenant context set, app_role queries return no rows (default-deny).

        The policy USING clause evaluates NULLIF(..., '')::uuid which returns
        NULL when the setting is absent — no rows match NULL, so none are visible
        to non-superuser roles that are subject to RLS.
        """
        raw = generate_raw_key()
        _tenant, _key = await create_tenant_with_key(
            async_session, name="Isolated", slug="rls-isolated", raw_key=raw
        )
        await async_session.commit()

        # Query as non-superuser with empty tenant context — should see no rows
        async with rls_session.begin():
            await rls_session.execute(text("SET LOCAL ROLE app_role"))
            await rls_session.execute(text("SET LOCAL app.current_tenant = ''"))
            result = await rls_session.execute(select(ApiKey))
            row = result.scalars().first()
            assert row is None, (
                "Expected no rows when app_role has empty app.current_tenant, "
                f"but got: {row!r}"
            )


# ===========================================================================
# TestAffiliateConfig
# ===========================================================================


class TestAffiliateConfig:
    """Tenant affiliate_enabled flag correctly reflected by is_affiliate_enabled()."""

    async def test_affiliate_enabled_false_suppresses_deeplinks(
        self, async_session: AsyncSession
    ) -> None:
        """Tenant with affiliate_enabled=False: is_affiliate_enabled returns False."""
        raw = generate_raw_key()
        tenant, _key = await create_tenant_with_key(
            async_session,
            name="No Affiliate",
            slug="aff-disabled",
            raw_key=raw,
            affiliate_enabled=False,
        )
        await async_session.commit()
        assert is_affiliate_enabled(tenant) is False

    async def test_affiliate_enabled_true_allows_deeplinks(
        self, async_session: AsyncSession
    ) -> None:
        """Tenant with affiliate_enabled=True: is_affiliate_enabled returns True."""
        raw = generate_raw_key()
        tenant, _key = await create_tenant_with_key(
            async_session,
            name="With Affiliate",
            slug="aff-enabled",
            raw_key=raw,
            affiliate_enabled=True,
        )
        await async_session.commit()
        assert is_affiliate_enabled(tenant) is True


# ===========================================================================
# TestRequestStatePopulation
# ===========================================================================


class TestRequestStatePopulation:
    """After resolve_key succeeds, the returned tenant has the expected attributes."""

    async def test_tenant_id_populated_after_resolve(
        self, async_session: AsyncSession
    ) -> None:
        """After resolve_key succeeds, the returned tenant has a UUID id."""
        raw = generate_raw_key()
        tenant, _key = await create_tenant_with_key(
            async_session, name="State Test", slug="state-test", raw_key=raw
        )
        await async_session.commit()

        resolved = await resolve_key(raw, async_session)
        assert resolved is not None, "resolve_key returned None for a key we just created"
        assert isinstance(resolved.id, uuid.UUID), (
            f"Expected UUID, got {type(resolved.id)}: {resolved.id!r}"
        )
        assert str(resolved.id) == str(tenant.id), (
            f"Resolved tenant id {resolved.id} != created tenant id {tenant.id}"
        )

    async def test_inventory_adapter_populated(
        self, async_session: AsyncSession
    ) -> None:
        """resolve_key returns a tenant with inventory_adapter set."""
        raw = generate_raw_key()
        _tenant, _key = await create_tenant_with_key(
            async_session,
            name="Adapter Test",
            slug="adapter-test",
            raw_key=raw,
            inventory_adapter="aviasales",
        )
        await async_session.commit()

        resolved = await resolve_key(raw, async_session)
        assert resolved is not None
        assert resolved.inventory_adapter == "aviasales"

    async def test_affiliate_flag_propagated_to_resolved_tenant(
        self, async_session: AsyncSession
    ) -> None:
        """Resolved tenant carries the affiliate_enabled flag (False case)."""
        raw = generate_raw_key()
        _tenant, _key = await create_tenant_with_key(
            async_session,
            name="Affiliate Flag Test",
            slug="aff-flag-test",
            raw_key=raw,
            affiliate_enabled=False,
        )
        await async_session.commit()

        resolved = await resolve_key(raw, async_session)
        assert resolved is not None
        assert resolved.affiliate_enabled is False


# ===========================================================================
# TestSeedDemoTenantForceRLS
# ===========================================================================


class TestSeedDemoTenantForceRLS:
    """seed_demo_tenant is idempotent when the session is a non-superuser under FORCE RLS.

    Production scenario: the app connects as a least-privilege app role (non-superuser),
    FORCE RLS is active, and there is no app.current_tenant context set at seed time.
    Under these conditions a SELECT-based idempotency check returns 0 rows even when the
    demo tenant already exists — the row is hidden by FORCE RLS. The insert-then-catch
    strategy (catch IntegrityError on slug unique constraint) is the correct fix.

    Option A2 (create_tenant_with_key bootstrap context management): the function
    auto-sets app.current_tenant to the new tenant's id before the INSERT, so
    INSERT...RETURNING succeeds under FORCE RLS without requiring the caller to manage
    context. Context is reset to "" after the flush; SELECT isolation is unchanged.

    These tests use app_role_session, which connects DIRECTLY as app_role (LOGIN
    non-superuser) — matching the production Cloud SQL posture. The previous SET ROLE
    implementation masked the INSERT...RETURNING FORCE-RLS bug; the direct-login path
    correctly exposes it and verifies the A2 fix.
    """

    async def test_app_role_session_is_non_superuser(
        self,
        app_role_session: AsyncSession,
    ) -> None:
        """Sanity check: the app_role_session fixture runs as a non-superuser.

        Superusers bypass FORCE RLS even when it is set; non-superusers don't.
        If this assertion fails the FORCE-RLS tests are meaningless.
        """
        is_superuser = await app_role_session.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        )
        assert is_superuser is False, (
            "app_role_session is running as a superuser — FORCE RLS will not apply. "
            "Check that the db_engine fixture created app_role without SUPERUSER."
        )

    async def test_double_seed_no_error_under_force_rls(
        self,
        app_role_session: AsyncSession,
        async_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two consecutive seed_demo_tenant calls as app_role must not raise.

        Under FORCE RLS with no tenant context, app_role cannot SELECT the existing
        demo tenant row (the row is hidden). The insert-then-catch strategy handles
        this: each call attempts the INSERT; the second (and any subsequent) call
        catches the IntegrityError on the slug unique constraint, rolls back, and
        returns cleanly. The count is verified via the superuser async_session, which
        bypasses RLS and always sees the real row count.
        """
        monkeypatch.setenv("DEMO_API_KEY", "rls-idempotent-test-key-abc123")

        # First call: either creates the tenant (INSERT succeeds) or finds it already
        # exists from a prior test in the session and catches IntegrityError — both are
        # valid; in both cases the function must not raise.
        await seed_demo_tenant(app_role_session)

        # Second call: the tenant now definitely exists; FORCE RLS hides it from
        # app_role (no tenant context), so a SELECT-based check would wrongly believe
        # it's absent. The insert-then-catch strategy must handle this correctly.
        await seed_demo_tenant(app_role_session)  # must not raise

        # Verify exactly one demo tenant using the superuser session.
        # We cannot use app_role_session here because FORCE RLS with no tenant
        # context would return 0 rows — the count would be wrong.
        result = await async_session.execute(
            select(func.count()).select_from(Tenant).where(Tenant.slug == "demo")
        )
        count = result.scalar()
        assert count == 1, (
            f"Expected exactly 1 demo tenant after double seed, found {count}. "
            "Duplicate rows indicate the IntegrityError was not caught correctly."
        )

    async def test_bootstrap_insert_returning_no_context(
        self,
        app_role_session: AsyncSession,
        async_session: AsyncSession,
    ) -> None:
        """A2: create_tenant_with_key succeeds via direct-login non-superuser with no context.

        Verifies the A2 bootstrap invariant:
          1. create_tenant_with_key via app_role_session (no prior context) — must succeed.
             A2 sets app.current_tenant to the new tenant's id before the INSERT so
             INSERT...RETURNING passes under FORCE RLS (non-superuser direct-login path).
          2. SELECT via app_role_session immediately after — must return 0 rows.
             A2 resets context to '' after flush; FORCE RLS default-deny applies to SELECT.
          3. Superuser confirms the row was persisted.

        Pre-A2 this path raised InsufficientPrivilegeError. A2 makes bootstrap
        provisioning self-contained without widening SELECT isolation.
        """
        raw_key = generate_raw_key()
        slug = f"boot-a2-{uuid.uuid4().hex[:8]}"

        # 1. Provision via app_role_session (no prior context) — must succeed.
        tenant, _api_key = await create_tenant_with_key(
            app_role_session,
            name="Bootstrap A2 Test",
            slug=slug,
            raw_key=raw_key,
        )
        assert tenant.id is not None
        await app_role_session.commit()

        # 2. SELECT via app_role_session with no context — must return 0 rows.
        count = await app_role_session.scalar(text("SELECT COUNT(*) FROM tenants"))
        assert count == 0, (
            f"No-context SELECT returned {count} rows after A2 bootstrap INSERT. "
            "A2 must reset context after flush; FORCE RLS default-deny must apply to SELECT."
        )

        # 3. Superuser confirms the row exists.
        persisted = await async_session.scalar(
            text("SELECT COUNT(*) FROM tenants WHERE id = :id").bindparams(id=tenant.id)
        )
        assert persisted == 1, "Bootstrap-inserted row should exist in DB (superuser view)."

        # Cleanup (cascades to api_keys via FK)
        await async_session.execute(
            text("DELETE FROM tenants WHERE id = :id").bindparams(id=tenant.id)
        )
        await async_session.commit()

    async def test_a2_guard_scoped_session_cannot_mint_different_tenant(
        self,
        app_role_session: AsyncSession,
        async_session: AsyncSession,
    ) -> None:
        """A2 guard: a session scoped to tenant A cannot create a new tenant B.

        If app.current_tenant is already set (runtime: request arrived with a valid
        API key), calling create_tenant_with_key for a DIFFERENT tenant must raise
        RuntimeError — not silently succeed or let the RLS WITH CHECK fire instead.

        This confirms A2's bootstrap encapsulation is not a cross-tenant write hole.
        """
        # Seed anchor tenant A (superuser path, no FORCE RLS)
        raw_a = generate_raw_key()
        tenant_a, _ = await create_tenant_with_key(
            async_session,
            name="Guard Anchor A",
            slug=f"guard-a-{uuid.uuid4().hex[:8]}",
            raw_key=raw_a,
        )
        await async_session.commit()

        try:
            # Scope app_role_session to tenant A, then try to create a NEW tenant.
            # A2 must detect prior_ctx != effective_id and refuse immediately.
            async with app_role_session.begin():
                await apply_rls_tenant(app_role_session, str(tenant_a.id))

                with pytest.raises(RuntimeError, match="context"):
                    await create_tenant_with_key(
                        app_role_session,
                        name="Guard Minted B",
                        slug=f"guard-b-{uuid.uuid4().hex[:8]}",
                        raw_key=generate_raw_key(),
                    )
        finally:
            await async_session.execute(
                text("DELETE FROM tenants WHERE id = :id").bindparams(id=tenant_a.id)
            )
            await async_session.commit()

    async def test_cross_tenant_insert_rejected_under_with_check(
        self,
        db_engine: AsyncEngine,
    ) -> None:
        """Tightened WITH CHECK blocks cross-tenant INSERT from a tenant-scoped session.

        Negative proof: a session with app.current_tenant = A cannot INSERT an
        api_key row whose tenant_id = B. This confirms that the bootstrap allowance
        (no tenant context → INSERT is permitted) does NOT open a hole for sessions
        that already have a tenant context set.
        """
        import uuid as _uuid

        tenant_a_id = _uuid.uuid4()
        tenant_b_id = _uuid.uuid4()

        # Setup: create two tenants as DB superuser (bypasses FORCE RLS for seeding)
        async with db_engine.begin() as setup_conn:
            await setup_conn.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, slug, inventory_adapter, affiliate_enabled, "
                    "rate_limit_tier, is_active, created_at, updated_at) VALUES "
                    "(:a_id, 'RLS Neg A', :a_slug, 'demo', true, 'standard', true, now(), now()),"
                    "(:b_id, 'RLS Neg B', :b_slug, 'demo', true, 'standard', true, now(), now())"
                ).bindparams(
                    a_id=tenant_a_id,
                    a_slug=f"rls-neg-a-{str(tenant_a_id)[:8]}",
                    b_id=tenant_b_id,
                    b_slug=f"rls-neg-b-{str(tenant_b_id)[:8]}",
                )
            )

        # Test: as app_role with tenant A context, attempt INSERT for tenant B.
        # WITH CHECK should reject it with a row-level security error.
        try:
            with pytest.raises(ProgrammingError, match="row-level security"):  # noqa: PT012
                async with db_engine.connect() as conn:
                    async with conn.begin():
                        # SET LOCAL ROLE: non-superuser, subject to FORCE RLS.
                        # SET LOCAL app.current_tenant: scopes the session to tenant A.
                        # Both are LOCAL (transaction-scoped) for clean cleanup.
                        await conn.execute(text("SET LOCAL ROLE app_role"))
                        # asyncpg rejects bound params for SET LOCAL — interpolate UUID
                        # directly (safe: UUIDs are hex+hyphens, no injection risk).
                        await conn.execute(
                            text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'")
                        )
                        await conn.execute(
                            text(
                                "INSERT INTO api_keys "
                                "(id, tenant_id, key_hash, key_prefix, is_active, created_at) "
                                "VALUES (:id, :tenant_id, :key_hash, :prefix, true, now())"
                            ).bindparams(
                                id=_uuid.uuid4(),
                                tenant_id=tenant_b_id,  # B's ID while session is A
                                key_hash="b" * 64,
                                prefix="tstpfx01",
                            )
                        )
        finally:
            # Always clean up the two test tenants regardless of pass/fail
            async with db_engine.begin() as cleanup_conn:
                await cleanup_conn.execute(
                    text(
                        "DELETE FROM tenants WHERE id = :a_id OR id = :b_id"
                    ).bindparams(a_id=tenant_a_id, b_id=tenant_b_id)
                )
