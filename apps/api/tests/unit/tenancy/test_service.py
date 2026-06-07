"""Unit tests for tenancy.service — pure/synchronous functions only.

DB-touching functions (resolve_key, create_tenant_with_key, seed_demo_tenant) are
exercised via mocked AsyncSession; we don't spin up a real Postgres instance here.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from travel_agent.tenancy.service import (
    generate_raw_key,
    hash_key,
    key_prefix,
    resolve_key,
    seed_demo_tenant,
)

# ── generate_raw_key ──────────────────────────────────────────────────────────


class TestGenerateRawKey:
    def test_returns_string(self) -> None:
        assert isinstance(generate_raw_key(), str)

    def test_length_is_43_chars(self) -> None:
        # secrets.token_urlsafe(32) → ceil(32 * 4/3) = 43 base64url chars
        key = generate_raw_key()
        assert len(key) == 43

    def test_keys_are_unique(self) -> None:
        keys = {generate_raw_key() for _ in range(20)}
        assert len(keys) == 20


# ── hash_key ──────────────────────────────────────────────────────────────────


class TestHashKey:
    def test_returns_64_hex_chars(self) -> None:
        result = hash_key("some-test-key")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic_without_pepper(self) -> None:
        """Same input must always produce the same digest."""
        raw = "dh_live_abcdefghijklmnopqrstuvwxyz012"
        assert hash_key(raw) == hash_key(raw)

    def test_plain_sha256_when_no_pepper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KEY_HASH_PEPPER", raising=False)
        raw = "test-key-no-pepper"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert hash_key(raw) == expected

    def test_pepper_changes_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        raw = "same-raw-key"
        monkeypatch.delenv("KEY_HASH_PEPPER", raising=False)
        without_pepper = hash_key(raw)

        monkeypatch.setenv("KEY_HASH_PEPPER", "mysecretpepper")
        with_pepper = hash_key(raw)

        assert without_pepper != with_pepper

    def test_peppered_digest_is_sha256_of_pepper_plus_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = "raw-key-value"
        pepper = "test-pepper"
        monkeypatch.setenv("KEY_HASH_PEPPER", pepper)
        expected = hashlib.sha256((pepper + raw).encode()).hexdigest()
        assert hash_key(raw) == expected


# ── key_prefix ────────────────────────────────────────────────────────────────


class TestKeyPrefix:
    def test_returns_first_8_chars(self) -> None:
        raw = "dh_live_abcdefghijklmnopqrstuvwxyz012"
        assert key_prefix(raw) == "dh_live_"

    def test_length_is_always_8(self) -> None:
        key = generate_raw_key()
        assert len(key_prefix(key)) == 8

    def test_prefix_never_contains_full_key(self) -> None:
        key = generate_raw_key()
        prefix = key_prefix(key)
        assert prefix != key


# ── resolve_key (mocked DB) ───────────────────────────────────────────────────


class TestResolveKey:
    @pytest.mark.asyncio
    async def test_returns_tenant_when_key_found(self) -> None:
        """resolve_key returns the Tenant object when a matching active key exists."""
        from travel_agent.tenancy.models import Tenant

        mock_tenant = MagicMock(spec=Tenant)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_tenant
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await resolve_key("some-valid-key", mock_session)

        assert result is mock_tenant
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_key_not_found(self) -> None:
        """resolve_key returns None when no matching key exists."""
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = None
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await resolve_key("nonexistent-key", mock_session)

        assert result is None


# ── seed_demo_tenant (mocked DB) ─────────────────────────────────────────────


class TestSeedDemoTenant:
    @pytest.mark.asyncio
    async def test_raises_when_demo_api_key_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEMO_API_KEY", raising=False)
        mock_session = AsyncMock()
        with pytest.raises(RuntimeError, match="DEMO_API_KEY"):
            await seed_demo_tenant(mock_session)

    @pytest.mark.asyncio
    async def test_no_op_when_demo_tenant_already_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """seed_demo_tenant must be idempotent — skips creation if slug=demo exists."""
        from travel_agent.tenancy.models import Tenant

        monkeypatch.setenv("DEMO_API_KEY", "test-demo-key-value-abc123xyz")

        mock_existing_tenant = MagicMock(spec=Tenant)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_existing_tenant
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()

        await seed_demo_tenant(mock_session)

        # commit must NOT have been called — nothing was created
        mock_session.commit.assert_not_awaited()
