from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import uuid

from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from travel_agent.persistence.engine import set_rls_tenant
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

    Two-step pattern, both steps under FORCE RLS as the (non-superuser, non-BYPASSRLS)
    app role — no superuser dependency and no RLS bypass:

    1. ``resolve_api_key_secure`` (SECURITY INVOKER) sets the transaction-local GUC
       ``app.bootstrap_key_hash`` and reads the single ``api_keys`` row whose UNIQUE
       ``key_hash`` matches. That read is permitted by the additive
       ``api_keys_bootstrap_auth`` policy — a row is visible IFF the caller presents its
       exact hash (the secret it already holds), so no cross-tenant visibility is
       granted. The function returns only the row's ``tenant_id`` and clears the GUC.
    2. The RLS context (``app.current_tenant``) is set to that tenant_id and the full
       Tenant is fetched through the normal ``id = app.current_tenant`` isolation policy.

    Returns the Tenant if the key is active and its tenant is active, else None. The
    tenant ``is_active`` check lives here (step 2) because the bootstrap resolver reads
    only ``api_keys``; an inactive tenant resolves a tenant_id but is rejected below.
    """
    key_hash_val = hash_key(raw_key)
    tenant_id: uuid.UUID | None = await session.scalar(
        text("SELECT resolve_api_key_secure(:kh)"),
        {"kh": key_hash_val},
    )
    if tenant_id is None:
        return None
    await set_rls_tenant(session, str(tenant_id))
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        return None
    return tenant


async def create_tenant_with_key(  # noqa: PLR0913
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    raw_key: str,
    description: str | None = None,
    tenant_id: uuid.UUID | None = None,
    inventory_adapter: str = "aviasales",
    affiliate_enabled: bool = True,
    rate_limit_tier: str = "standard",
) -> tuple[Tenant, ApiKey]:
    """Create a tenant and its first API key atomically.

    The raw_key is hashed before storage. Returns (Tenant, ApiKey).
    The caller must commit the session.

    A2 — bootstrap context management: if no RLS context is currently set (the
    normal provisioning/seed path), the function temporarily sets app.current_tenant
    to the new tenant's id via the validated set_rls_tenant path so INSERT...RETURNING
    succeeds under FORCE RLS on non-superuser connections. Context is reset to ""
    before returning so subsequent calls on the same session each manage their own
    context window.

    Guard: if a context IS already set and it differs from the new tenant's id, raises
    RuntimeError — tenant provisioning must run from a context-free session, not inside
    an active tenant scope. The RLS WITH CHECK policy enforces the same invariant at
    the DB layer; this guard surfaces it earlier with a clear message.
    """
    effective_id = tenant_id if tenant_id is not None else uuid.uuid4()

    prior_ctx: str = (
        await session.scalar(text("SELECT current_setting('app.current_tenant', true)"))
    ) or ""

    context_set_here = False
    if prior_ctx:
        if prior_ctx != str(effective_id):
            msg = (
                f"Session is already scoped to tenant {prior_ctx}; cannot provision "
                f"new tenant {effective_id} from within an active tenant context. "
                "Call create_tenant_with_key from a context-free session."
            )
            raise RuntimeError(msg)
        # prior_ctx == effective_id: unusual re-entry; proceed without re-setting
    else:
        await set_rls_tenant(session, str(effective_id))
        context_set_here = True

    try:
        tenant = Tenant(
            id=effective_id,
            name=name,
            slug=slug,
            inventory_adapter=inventory_adapter,
            affiliate_enabled=affiliate_enabled,
            rate_limit_tier=rate_limit_tier,
        )
        session.add(tenant)
        await session.flush()

        api_key = ApiKey(
            tenant_id=tenant.id,
            key_hash=hash_key(raw_key),
            key_prefix=key_prefix(raw_key),
            description=description,
            is_active=True,
        )
        session.add(api_key)
        await session.flush()  # flush api_key under the correct context before resetting

        return tenant, api_key
    finally:
        if context_set_here:
            with contextlib.suppress(Exception):
                await session.execute(text("SET LOCAL app.current_tenant = ''"))
                # contextlib.suppress silences the execute if the session is in an
                # error state (e.g., flush raised IntegrityError) — the caller's
                # rollback resets SET LOCAL context automatically in that case.


async def _ensure_demo_affiliate_disabled(session: AsyncSession, raw_key: str) -> None:
    """Ensure the demo tenant's affiliate_enabled is False.

    Uses the bootstrap resolver (not a bare context-less query) so the UPDATE
    runs within the correct RLS tenant scope. Idempotent: skips the UPDATE if
    affiliate_enabled is already False.
    """
    key_hash_val = hash_key(raw_key)
    tenant_id: uuid.UUID | None = await session.scalar(
        text("SELECT resolve_api_key_secure(:kh)"), {"kh": key_hash_val}
    )
    if tenant_id is None:
        return

    await set_rls_tenant(session, str(tenant_id))

    current: bool | None = await session.scalar(
        text("SELECT affiliate_enabled FROM tenants WHERE id = :tid"),
        {"tid": str(tenant_id)},
    )
    if not current:
        await session.commit()
        return

    await session.execute(
        text("UPDATE tenants SET affiliate_enabled = false WHERE id = :tid"),
        {"tid": str(tenant_id)},
    )

    after: bool | None = await session.scalar(
        text("SELECT affiliate_enabled FROM tenants WHERE id = :tid"),
        {"tid": str(tenant_id)},
    )
    if after is not False:
        msg = f"affiliate_enabled update failed for demo tenant {tenant_id}"
        raise RuntimeError(msg)

    await session.commit()


async def seed_demo_tenant(session: AsyncSession) -> None:
    """Idempotently ensure a demo tenant + the DEMO_API_KEY exist.

    Safe to call on every startup, including under FORCE ROW LEVEL SECURITY.
    Uses insert-then-catch rather than check-then-insert: under FORCE RLS with
    no app.current_tenant set, a SELECT returns zero rows even when the demo
    tenant already exists, causing a false "not found" on every restart. We
    attempt the INSERT directly and treat IntegrityError (slug unique constraint)
    as "already seeded".
    Raises RuntimeError if DEMO_API_KEY is not set.
    """
    raw_key = os.environ.get("DEMO_API_KEY")
    if not raw_key:
        msg = "DEMO_API_KEY environment variable must be set"
        raise RuntimeError(msg)

    try:
        await create_tenant_with_key(
            session,
            name="Demo",
            slug="demo",
            raw_key=raw_key,
            description="Seeded demo tenant — backward compat with DEMO_API_KEY",
            inventory_adapter="demo",
            affiliate_enabled=False,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await _ensure_demo_affiliate_disabled(session, raw_key)
    except ProgrammingError as exc:
        # Defensive: guard against RLS WITH CHECK rejection on INSERT. This path
        # fires when the DB schema predates the tightened WITH CHECK migration or
        # an unexpected policy configuration rejects the bootstrap INSERT.
        if "row-level security" not in str(exc).lower():
            raise
        await session.rollback()
