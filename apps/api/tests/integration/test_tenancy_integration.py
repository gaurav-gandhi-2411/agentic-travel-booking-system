"""Integration tests for tenancy: demo-key backward compat, RLS isolation,
affiliate config, and RequestState population.

All tests run against a real Postgres 16 container (see conftest.py).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
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
