from __future__ import annotations

"""One-shot admin script: set demo tenant affiliate_enabled=False in Supabase.

Run from apps/api/ with the app-role DATABASE_URL (same URL the running backend
uses — non-superuser, non-BYPASSRLS):

    $env:DATABASE_URL  = "postgresql://<app-role>:<pw>@<host>/<db>?sslmode=require"
    $env:DEMO_API_KEY  = "<demo-api-key>"
    python -m scripts.patch_demo_tenant_affiliate

Uses resolve_api_key_secure + SET LOCAL app.current_tenant — no bare context-less
UPDATE. Reads before/after and asserts the change, then exits 0 on success.
Idempotent: exits 0 (no-op) if affiliate_enabled is already False.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from sqlalchemy.sql import text

from travel_agent.persistence.engine import get_session_factory, set_rls_tenant
from travel_agent.tenancy.service import hash_key


async def main() -> int:
    load_dotenv(find_dotenv(usecwd=True))
    root_env = Path(__file__).resolve().parents[3] / ".env"
    if root_env.exists():
        load_dotenv(root_env)

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL must be set (the app-role connection string).")
        return 2
    raw_key = os.environ.get("DEMO_API_KEY")
    if not raw_key:
        print("ERROR: DEMO_API_KEY must be set.")
        return 2

    factory = get_session_factory()

    async with factory() as session:
        key_hash_val = hash_key(raw_key)
        tenant_id = await session.scalar(
            text("SELECT resolve_api_key_secure(:kh)"), {"kh": key_hash_val}
        )
        if tenant_id is None:
            print("ERROR: DEMO_API_KEY did not resolve to any tenant. Is the key correct?")
            return 2

        await set_rls_tenant(session, str(tenant_id))

        before: bool | None = await session.scalar(
            text("SELECT affiliate_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
        print(f"  demo tenant id        : {tenant_id}")
        print(f"  affiliate_enabled BEFORE : {before}")

        if not before:
            print("  already False — no change needed.")
            await session.rollback()
            return 0

        await session.execute(
            text("UPDATE tenants SET affiliate_enabled = false WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )

        after: bool | None = await session.scalar(
            text("SELECT affiliate_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
        print(f"  affiliate_enabled AFTER  : {after}")

        if after is not False:
            print("ERROR: affiliate_enabled did not change — UPDATE failed.")
            await session.rollback()
            return 1

        await session.commit()
        print("  VERIFIED: affiliate_enabled True -> False, committed.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
