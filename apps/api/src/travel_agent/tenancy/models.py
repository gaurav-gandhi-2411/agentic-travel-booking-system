from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from travel_agent.persistence.schema import DB_SCHEMA


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """`{DB_SCHEMA}.tenants` — schema explicit, not search_path-derived.

    Explicit schema qualification (rather than relying on the connection's
    ambient search_path) is what makes this table safe to query over the
    Supabase transaction pooler: transaction-mode connections can be handed
    to a different logical session between transactions, so a search_path
    pinned only at connection-open time isn't guaranteed to still apply.
    Fully-qualified SQL has no such dependency. See ADR-0028.
    """

    __tablename__ = "tenants"
    __table_args__ = {"schema": DB_SCHEMA}  # noqa: RUF012 -- SQLAlchemy dunder, not a mutable-default hazard

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    # Per-tenant config consumed by the pipeline (Step 4)
    inventory_adapter: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="aviasales"
    )
    affiliate_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    rate_limit_tier: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="standard"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    api_keys: Mapped[list[ApiKey]] = relationship(
        "ApiKey", back_populates="tenant", cascade="all, delete-orphan"
    )


class ApiKey(Base):
    """`{DB_SCHEMA}.api_keys` — schema explicit, not search_path-derived. See Tenant."""

    __tablename__ = "api_keys"
    __table_args__ = {"schema": DB_SCHEMA}  # noqa: RUF012 -- SQLAlchemy dunder, not a mutable-default hazard

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest of the raw key — plaintext never persisted
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # First 8 chars of the raw key — safe to store, used for display/hint only
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="api_keys")
