from __future__ import annotations

import hashlib
import os
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.tenancy.models import ApiKey, Tenant

# Raw keys are 32 URL-safe random bytes → 43-char base64url string.
# The prefix (first 8 chars) is stored for display; the full key is never persisted.
_KEY_BYTES = 32
_PREFIX_LEN = 8


def generate_raw_key() -> str:
    """Return a cryptographically random URL-safe API key string."""
    return secrets.token_urlsafe(_KEY_BYTES)


def hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of raw_key.

    An HMAC with a pepper (KEY_HASH_PEPPER env var) is used when the env var is
    set. Falls back to plain SHA-256 when unset (acceptable for local dev; pepper
    should be set in production).
    """
    pepper = os.environ.get("KEY_HASH_PEPPER", "")
    if pepper:
        digest = hashlib.sha256((pepper + raw_key).encode()).hexdigest()
    else:
        digest = hashlib.sha256(raw_key.encode()).hexdigest()
    return digest


def key_prefix(raw_key: str) -> str:
    """Return the first 8 characters of the raw key (safe to store/display)."""
    return raw_key[:_PREFIX_LEN]


async def resolve_key(raw_key: str, session: AsyncSession) -> Tenant | None:
    """Look up the tenant for a raw API key.

    Returns the Tenant if the key is active and its tenant is active, else None.
    This query runs BEFORE the RLS session var is set — the caller is responsible
    for setting app.current_tenant after a successful resolution.
    """
    key_hash_val = hash_key(raw_key)
    result = await session.execute(
        select(Tenant)
        .join(ApiKey, ApiKey.tenant_id == Tenant.id)
        .where(
            ApiKey.key_hash == key_hash_val,
            ApiKey.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
    )
    return result.scalars().first()


async def create_tenant_with_key(  # noqa: PLR0913
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    raw_key: str,
    description: str | None = None,
    inventory_adapter: str = "aviasales",
    affiliate_enabled: bool = True,
    rate_limit_tier: str = "standard",
) -> tuple[Tenant, ApiKey]:
    """Create a tenant and its first API key atomically.

    The raw_key is hashed before storage. Returns (Tenant, ApiKey).
    The caller must commit the session.
    """
    tenant = Tenant(
        name=name,
        slug=slug,
        inventory_adapter=inventory_adapter,
        affiliate_enabled=affiliate_enabled,
        rate_limit_tier=rate_limit_tier,
    )
    session.add(tenant)
    await session.flush()  # populate tenant.id

    api_key = ApiKey(
        tenant_id=tenant.id,
        key_hash=hash_key(raw_key),
        key_prefix=key_prefix(raw_key),
        description=description,
        is_active=True,
    )
    session.add(api_key)
    return tenant, api_key


async def seed_demo_tenant(session: AsyncSession) -> None:
    """Idempotently ensure a demo tenant + the DEMO_API_KEY exist.

    Safe to call on every startup. No-op if the demo slug already exists.
    Raises RuntimeError if DEMO_API_KEY is not set.
    """
    raw_key = os.environ.get("DEMO_API_KEY")
    if not raw_key:
        msg = "DEMO_API_KEY environment variable must be set"
        raise RuntimeError(msg)

    existing = await session.execute(select(Tenant).where(Tenant.slug == "demo"))
    if existing.scalars().first() is not None:
        return  # already seeded

    await create_tenant_with_key(
        session,
        name="Demo",
        slug="demo",
        raw_key=raw_key,
        description="Seeded demo tenant — backward compat with DEMO_API_KEY",
    )
    await session.commit()
