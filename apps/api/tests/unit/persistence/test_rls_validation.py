"""Unit tests for RLS validation: _validate_tenant_id and SET LOCAL boundary.

Pure unit tests — no database needed. Verifies that injection-shaped inputs are
rejected before any SQL is produced.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from travel_agent.persistence.engine import set_rls_tenant
from travel_agent.persistence.rls import _validate_tenant_id, apply_rls_tenant


# ===========================================================================
# TestValidateTenantId
# ===========================================================================


class TestValidateTenantId:
    """_validate_tenant_id rejects bad inputs and accepts canonical UUIDs."""

    def test_rejects_plain_string(self) -> None:
        with pytest.raises(ValueError):
            _validate_tenant_id("not-a-uuid")

    def test_rejects_sql_injection_attempt(self) -> None:
        with pytest.raises(ValueError):
            _validate_tenant_id("'; DROP TABLE tenants; --")

    def test_rejects_uuid_with_appended_sql(self) -> None:
        with pytest.raises(ValueError):
            _validate_tenant_id("00000000-0000-0000-0000-000000000000'; DROP TABLE tenants;")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError):
            _validate_tenant_id("")

    def test_rejects_whitespace(self) -> None:
        with pytest.raises(ValueError):
            _validate_tenant_id("   ")

    def test_rejects_uuid_with_extra_chars(self) -> None:
        with pytest.raises(ValueError):
            _validate_tenant_id("00000000-0000-0000-0000-000000000000x")

    def test_accepts_valid_uuid(self) -> None:
        uid = "550e8400-e29b-41d4-a716-446655440000"
        result = _validate_tenant_id(uid)
        assert result == uid

    def test_accepts_uuid4(self) -> None:
        uid = str(uuid.uuid4())
        assert _validate_tenant_id(uid) == uid

    def test_output_is_canonical_hyphenated_form(self) -> None:
        """uuid.UUID normalises to lowercase hyphenated form."""
        uid = "550E8400-E29B-41D4-A716-446655440000"  # uppercase input
        result = _validate_tenant_id(uid)
        assert result == uid.lower()


# ===========================================================================
# TestApplyRlsTenantValidation
# ===========================================================================


class TestApplyRlsTenantValidation:
    """apply_rls_tenant must reject invalid tenant_id before reaching SQL."""

    @pytest.mark.asyncio
    async def test_rejects_injection_before_sql(self) -> None:
        """ValueError raised before session.execute is ever awaited."""
        mock_session = AsyncMock()
        with pytest.raises(ValueError):
            await apply_rls_tenant(mock_session, "'; DROP TABLE tenants; --")
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_empty_string_before_sql(self) -> None:
        mock_session = AsyncMock()
        with pytest.raises(ValueError):
            await apply_rls_tenant(mock_session, "")
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_uuid_with_extra_chars_before_sql(self) -> None:
        mock_session = AsyncMock()
        with pytest.raises(ValueError):
            await apply_rls_tenant(mock_session, "00000000-0000-0000-0000-000000000000x")
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepts_valid_uuid_and_calls_execute(self) -> None:
        """Valid UUID passes validation and session.execute is awaited."""
        mock_session = AsyncMock()
        uid = str(uuid.uuid4())
        await apply_rls_tenant(mock_session, uid)
        mock_session.execute.assert_awaited_once()


# ===========================================================================
# TestSetRlsTenantValidation
# ===========================================================================


class TestSetRlsTenantValidation:
    """set_rls_tenant must reject invalid tenant_id before reaching SQL."""

    @pytest.mark.asyncio
    async def test_rejects_injection_before_sql(self) -> None:
        mock_session = AsyncMock()
        with pytest.raises(ValueError):
            await set_rls_tenant(mock_session, "'; DROP TABLE tenants; --")
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_empty_string_before_sql(self) -> None:
        mock_session = AsyncMock()
        with pytest.raises(ValueError):
            await set_rls_tenant(mock_session, "")
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_plain_string_before_sql(self) -> None:
        mock_session = AsyncMock()
        with pytest.raises(ValueError):
            await set_rls_tenant(mock_session, "not-a-uuid")
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepts_valid_uuid_and_calls_execute(self) -> None:
        """Valid UUID passes validation and session.execute is awaited."""
        mock_session = AsyncMock()
        uid = str(uuid.uuid4())
        await set_rls_tenant(mock_session, uid)
        mock_session.execute.assert_awaited_once()
