"""Bootstrap-auth RLS policy + SECURITY INVOKER resolver (no superuser, no BYPASSRLS).

Context — why this replaces the BYPASSRLS-owner approach
--------------------------------------------------------
The auth resolver ``resolve_api_key_secure`` must, given an API key, find that key's
single ``api_keys`` row by its UNIQUE ``key_hash`` and return the row's ``tenant_id`` —
*before* any ``app.current_tenant`` context exists (the bootstrap of the request). Under
``FORCE ROW LEVEL SECURITY`` a non-superuser, non-BYPASSRLS caller sees zero rows with no
context, so that bootstrap read needs a legitimate way through the policy layer.

Migration ``b2c3d4e5f6a7`` made the resolver ``SECURITY DEFINER`` and relied on its OWNER
bypassing RLS. That only works if the owner is a superuser or a ``BYPASSRLS`` role.
**Managed free Postgres (Neon / Supabase) gives no superuser and forbids creating a
``BYPASSRLS`` role** (the creator must itself hold ``BYPASSRLS``). So the
SECURITY-DEFINER-owner approach — and the dedicated ``dealhunter_resolver`` owner this
revision originally created — cannot run there. The migration chain itself breaks at the
``CREATE ROLE ... BYPASSRLS`` step on a non-superuser cluster.

The isolation-preserving redesign (no superuser, no BYPASSRLS, FORCE RLS intact)
---------------------------------------------------------------------------------
1. An additive **PERMISSIVE** policy ``api_keys_bootstrap_auth`` (``FOR SELECT`` only)
   permits reading an ``api_keys`` row IFF the caller has placed that row's exact
   ``key_hash`` into the transaction-local GUC ``app.bootstrap_key_hash``.

   ``key_hash`` is ``UNIQUE`` and is the SHA-256 of a 256-bit random secret, so this
   exposes **at most one row**, and only to a caller who already holds that secret.
   Nothing is enumerable (no ``true``, no range, no ``LIKE``/prefix — equality on a
   high-entropy unique column only), and there is no cross-tenant scan. When the GUC is
   unset — i.e. *all* normal tenant traffic — the predicate is ``key_hash = NULL`` →
   false for every row, so the policy contributes **zero** added visibility and SELECT
   isolation is byte-for-byte identical to before.

   PERMISSIVE policies for the same command combine with OR, so for SELECT the effective
   predicate becomes:
       (tenant_id = app.current_tenant)  OR  (key_hash = app.bootstrap_key_hash)
   The bootstrap term is SELECT-only; it never touches the FOR ALL policy's
   INSERT/UPDATE/DELETE USING or WITH CHECK, so write isolation is untouched.

2. The resolver becomes **SECURITY INVOKER** (default): it runs as the *calling* app
   role, which stays non-superuser, non-BYPASSRLS and fully bound by FORCE RLS. It sets
   the bootstrap GUC via ``set_config(..., is_local => true)`` — the function form of
   ``SET LOCAL``, fully parameterized (no string interpolation anywhere), performs the
   unique-hash lookup that ``api_keys_bootstrap_auth`` permits, clears the GUC, and
   returns the ``tenant_id``. The ``tenants`` table needs no bootstrap policy: the caller
   sets ``app.current_tenant`` from this result and reads the tenant row through the
   normal ``id = app.current_tenant`` isolation policy; ``is_active`` is checked in Python.

Net: FORCE ROW LEVEL SECURITY stays ON for both tables; ``dealhunter_app`` (or any app
role) stays a non-superuser, non-owner, fully-policed runtime role; no role in the system
has BYPASSRLS or superuser. Tenant isolation is unchanged — the only added read is one
row, gated by possession of that row's own unique secret.

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Additive bootstrap-auth policy: reveal a row only to a caller presenting its
    #    exact unique key_hash. NULLIF coerces the unset/empty GUC to NULL so the
    #    predicate is false (zero rows) during all normal, non-bootstrap traffic.
    op.execute(
        """
        CREATE POLICY api_keys_bootstrap_auth ON api_keys
        FOR SELECT
        USING (
            key_hash = NULLIF(current_setting('app.bootstrap_key_hash', true), '')
        )
        """
    )

    # 2. Resolver as SECURITY INVOKER — runs as the app role, fully RLS-bound. It sets
    #    the bootstrap GUC (transaction-local, parameterized), reads the one permitted
    #    row, clears the GUC, and returns the tenant_id. No superuser / BYPASSRLS / owner
    #    bypass involved. CREATE OR REPLACE supersedes the b2c3 SECURITY DEFINER version.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION resolve_api_key_secure(p_key_hash text)
        RETURNS uuid
        SECURITY INVOKER
        SET search_path = dealhunter
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_tenant_id uuid;
        BEGIN
            PERFORM set_config('app.bootstrap_key_hash', p_key_hash, true);
            SELECT k.tenant_id
            INTO   v_tenant_id
            FROM   api_keys k
            WHERE  k.key_hash  = p_key_hash
              AND  k.is_active = true
            LIMIT 1;
            PERFORM set_config('app.bootstrap_key_hash', '', true);
            RETURN v_tenant_id;
        END;
        $$
        """
    )


def downgrade() -> None:
    # Restore the b2c3 SECURITY DEFINER resolver and drop the bootstrap policy.
    op.execute("DROP POLICY IF EXISTS api_keys_bootstrap_auth ON api_keys")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION resolve_api_key_secure(p_key_hash text)
        RETURNS uuid
        SECURITY DEFINER
        SET search_path = dealhunter
        LANGUAGE sql
        AS $$
            SELECT t.id
            FROM   api_keys k
            JOIN   tenants  t ON t.id = k.tenant_id
            WHERE  k.key_hash  = p_key_hash
              AND  k.is_active = true
              AND  t.is_active = true
            LIMIT 1;
        $$
        """
    )
