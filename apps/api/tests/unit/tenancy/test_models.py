"""Unit tests for Tenant and ApiKey SQLAlchemy models.

These tests exercise the Python-side behaviour of the ORM models without
requiring a live database connection:  table metadata introspection, default
value callables, relationship configuration, and the security invariant that
no plaintext key column exists on ApiKey.
"""

from __future__ import annotations

import pytest

from travel_agent.tenancy.models import ApiKey, Base, Tenant

# ── table metadata helpers ────────────────────────────────────────────────────


def _column_names(model: type) -> set[str]:
    """Return the set of column names declared on an ORM-mapped class."""
    return {col.key for col in model.__table__.columns}  # type: ignore[attr-defined]


# ── Tenant model ──────────────────────────────────────────────────────────────


class TestTenantModel:
    def test_tablename(self) -> None:
        assert Tenant.__tablename__ == "tenants"

    def test_required_columns_present(self) -> None:
        cols = _column_names(Tenant)
        expected = {
            "id",
            "name",
            "slug",
            "inventory_adapter",
            "affiliate_enabled",
            "rate_limit_tier",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(cols)

    def test_slug_has_unique_constraint(self) -> None:
        slug_col = Tenant.__table__.c["slug"]  # type: ignore[attr-defined]
        assert slug_col.unique is True

    def test_id_default_is_uuid4_callable(self) -> None:
        """id default must be uuid.uuid4 so each row gets a fresh UUID.

        SQLAlchemy wraps the callable in a ColumnDefault that injects a context
        argument at execution time.  We verify by name/module rather than identity
        because the function object captured in the column definition may differ
        from the one in the test's uuid namespace at import time.
        """
        id_col = Tenant.__table__.c["id"]  # type: ignore[attr-defined]
        fn = id_col.default.arg  # type: ignore[union-attr]
        assert callable(fn)
        assert fn.__name__ == "uuid4"
        assert fn.__module__ == "uuid"

    def test_api_keys_relationship_exists(self) -> None:
        rel = Tenant.__mapper__.relationships["api_keys"]  # type: ignore[attr-defined]
        assert rel.cascade.delete_orphan is True

    def test_base_is_shared(self) -> None:
        """Tenant and ApiKey must share the same Base so metadata is unified."""
        assert Tenant.metadata is ApiKey.metadata  # type: ignore[attr-defined]
        assert Tenant.metadata is Base.metadata  # type: ignore[attr-defined]


# ── ApiKey model ──────────────────────────────────────────────────────────────


class TestApiKeyModel:
    def test_tablename(self) -> None:
        assert ApiKey.__tablename__ == "api_keys"

    def test_no_plaintext_key_column(self) -> None:
        """Security invariant: a column named 'key' must never exist."""
        cols = _column_names(ApiKey)
        assert "key" not in cols, (
            "Plaintext 'key' column found on ApiKey — store key_hash only"
        )

    def test_key_hash_length_constraint(self) -> None:
        """key_hash is a SHA-256 hex digest: exactly 64 characters."""
        key_hash_col = ApiKey.__table__.c["key_hash"]  # type: ignore[attr-defined]
        assert key_hash_col.type.length == 64  # type: ignore[attr-defined]

    def test_key_prefix_length_constraint(self) -> None:
        key_prefix_col = ApiKey.__table__.c["key_prefix"]  # type: ignore[attr-defined]
        assert key_prefix_col.type.length == 8  # type: ignore[attr-defined]

    def test_key_hash_has_unique_constraint(self) -> None:
        col = ApiKey.__table__.c["key_hash"]  # type: ignore[attr-defined]
        assert col.unique is True

    def test_tenant_id_foreign_key_cascade(self) -> None:
        col = ApiKey.__table__.c["tenant_id"]  # type: ignore[attr-defined]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        fk = fks[0]
        assert fk.ondelete == "CASCADE"

    def test_description_is_nullable(self) -> None:
        col = ApiKey.__table__.c["description"]  # type: ignore[attr-defined]
        assert col.nullable is True

    def test_last_used_at_is_nullable(self) -> None:
        col = ApiKey.__table__.c["last_used_at"]  # type: ignore[attr-defined]
        assert col.nullable is True

    def test_tenant_relationship_back_populates(self) -> None:
        rel = ApiKey.__mapper__.relationships["tenant"]  # type: ignore[attr-defined]
        assert rel.back_populates == "api_keys"


# ── cross-model ───────────────────────────────────────────────────────────────


def test_metadata_contains_both_tables() -> None:
    """Base.metadata must reflect both tables so Alembic autogenerate works."""
    table_names = set(Base.metadata.tables.keys())
    assert "tenants" in table_names
    assert "api_keys" in table_names


@pytest.mark.parametrize(
    ("raw_key", "expected_hash_len", "expected_prefix"),
    [
        ("dh_live_abcdefghijklmnop", 64, "dh_live_"),
        ("sk-test-XYZXYZXYZXYZ0000", 64, "sk-test-"),
    ],
)
def test_key_hash_derivation_contract(
    raw_key: str, expected_hash_len: int, expected_prefix: str
) -> None:
    """Demonstrate the hashing + prefix convention without touching the DB.

    The model layer does not hash; this test validates that the field lengths
    are wide enough to hold a SHA-256 hex digest and an 8-char prefix.
    """
    import hashlib

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]

    assert len(key_hash) == expected_hash_len
    assert key_prefix == expected_prefix

    # Verify these values fit within the declared column constraints.
    assert len(key_hash) <= ApiKey.__table__.c["key_hash"].type.length  # type: ignore[attr-defined]
    assert len(key_prefix) <= ApiKey.__table__.c["key_prefix"].type.length  # type: ignore[attr-defined]
