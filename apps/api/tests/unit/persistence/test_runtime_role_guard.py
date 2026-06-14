"""Unit tests for the runtime-role startup guard.

assert_runtime_role_unprivileged refuses to start when the connected DB role can bypass
RLS (rolsuper or rolbypassrls). This is the structural enforcement of "the app must never
serve traffic as the platform admin/superuser role" — on Supabase the 'postgres' role has
BYPASSRLS, which would silently void tenant isolation if used as the app connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from travel_agent.persistence.engine import assert_runtime_role_unprivileged


def _session_returning(role: str, rolsuper: bool, rolbypassrls: bool) -> MagicMock:
    """Build a fake AsyncSession whose pg_roles query returns the given role flags."""
    result = MagicMock()
    result.one.return_value = (role, rolsuper, rolbypassrls)
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


async def test_guard_passes_for_policed_role() -> None:
    """A non-superuser, non-BYPASSRLS role (the deployed app role) starts cleanly."""
    session = _session_returning("dealhunter_app", rolsuper=False, rolbypassrls=False)
    await assert_runtime_role_unprivileged(session)  # must not raise


async def test_guard_refuses_bypassrls_role() -> None:
    """A BYPASSRLS role (e.g. Supabase 'postgres') is refused — isolation would be void."""
    session = _session_returning("postgres", rolsuper=False, rolbypassrls=True)
    with pytest.raises(RuntimeError, match="BYPASS"):
        await assert_runtime_role_unprivileged(session)


async def test_guard_refuses_superuser_role() -> None:
    """A superuser role is refused (superusers bypass RLS by design)."""
    session = _session_returning("admin", rolsuper=True, rolbypassrls=False)
    with pytest.raises(RuntimeError, match="REFUSING TO START"):
        await assert_runtime_role_unprivileged(session)
