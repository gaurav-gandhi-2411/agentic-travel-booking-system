"""Live re-verification: resolve_key() + RLS isolation over the Supabase
TRANSACTION pooler (port 6543), after schema-qualifying Tenant/ApiKey (ADR-0028).

Focused re-proof, NOT a full re-run of the original bootstrap verification
(scripts/verify_resolver_free_pg.py) -- migrations, role provisioning, and
blast-radius checks don't depend on pooler mode and are unchanged. This script
covers exactly what the schema-qualification + pooler-mode switch could affect:

  1. resolve_key() end-to-end (SECURITY DEFINER bootstrap function + the now-
     schema-qualified ORM read) actually works when the app role connects over
     the transaction pooler instead of the session pooler.
  2. RLS cross-tenant isolation (SELECT/UPDATE/DELETE/INSERT + bootstrap
     exposure) still holds under that connection -- a schema-qualification
     change touching the auth path must re-prove isolation, not just "it runs".

No new role is provisioned: DATABASE_URL in prod Secret Manager is already
bound to the real least-privilege `dealhunter_app` role (SELECT/INSERT/UPDATE/
DELETE on tenants+api_keys, EXECUTE on the resolver, no DDL) -- the same role
works over the transaction pooler, just on a different port. Two temporary
test tenants are created and deleted at the end; nothing else in the schema is
touched, and the schema itself is never dropped (unlike the bootstrap script,
this runs against a LIVE prod schema with real data).

Usage (PowerShell):
    $env:DATABASE_URL = "<the real prod session-pooler URL, port 5432>"
    python -m scripts.verify_transaction_pooler_isolation

The transaction-pooler URL is derived from DATABASE_URL by swapping the port
5432 -> 6543 (same host, same role, same credentials -- Supabase's session and
transaction poolers differ only by port). Pass TRANSACTION_POOLER_DATABASE_URL
explicitly to override this derivation if the two ever diverge.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any, cast

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import CursorResult, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import text

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scripts.verify_resolver_free_pg import _asyncpg_url, _role_flags  # noqa: E402
from travel_agent.persistence.schema import DB_SCHEMA  # noqa: E402
from travel_agent.tenancy.service import (  # noqa: E402
    create_tenant_with_key,
    generate_raw_key,
    hash_key,
    resolve_key,
)

_TQ_TENANTS = f"{DB_SCHEMA}.tenants"
_TQ_API_KEYS = f"{DB_SCHEMA}.api_keys"
_SESSION_POOLER_PORT = 5432
_TRANSACTION_POOLER_PORT = 6543


def _derive_transaction_pooler_url(session_pooler_url: str) -> str:
    u = make_url(_asyncpg_url(session_pooler_url))
    if u.port != _SESSION_POOLER_PORT:
        msg = f"Expected DATABASE_URL on port {_SESSION_POOLER_PORT} (session pooler), got {u.port}"
        raise ValueError(msg)
    return u.set(port=_TRANSACTION_POOLER_PORT).render_as_string(hide_password=False)


def _make_transaction_pooler_engine(url: str) -> AsyncEngine:
    """App-role engine over the transaction pooler -- deliberately NO search_path
    server_setting. This is the exact thing being verified: Tenant/ApiKey are
    schema-qualified (tenancy/models.py) and resolve_api_key_secure has its own
    bound search_path (migration b2c3d4e5f6a7), so nothing here should depend
    on the connection's ambient search_path -- matches persistence/engine.py.
    """
    u = make_url(url)
    connect_args: dict[str, object] = {}
    if u.host and "pooler.supabase.com" in u.host:
        connect_args["statement_cache_size"] = 0  # Supavisor: no prepared statements
    return create_async_engine(u, pool_pre_ping=True, connect_args=connect_args)


async def _rowcount(session: AsyncSession, sql: str, tenant_id: uuid.UUID) -> int:
    result = cast("CursorResult[Any]", await session.execute(text(sql), {"id": tenant_id}))
    return result.rowcount


async def _count_visible(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            text(f"SELECT COUNT(*) FROM {_TQ_API_KEYS} WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        or 0
    )


async def main() -> int:  # noqa: PLR0915
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv(find_dotenv(usecwd=True))
    root_env = Path(__file__).resolve().parents[3] / ".env"
    if root_env.exists():
        load_dotenv(root_env)

    session_url = os.environ.get("DATABASE_URL")
    if not session_url:
        print("ERROR: DATABASE_URL must be set (the real prod session-pooler URL).")
        return 2

    txn_url = os.environ.get("TRANSACTION_POOLER_DATABASE_URL") or _derive_transaction_pooler_url(
        session_url
    )

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    app_engine = _make_transaction_pooler_engine(txn_url)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)

    actual_name, actual_super, actual_bypass = await _role_flags(app_engine)
    check(
        "app role over TRANSACTION pooler is non-superuser, non-BYPASSRLS",
        actual_super is False and actual_bypass is False,
        f"current_user={actual_name}",
    )

    suffix = uuid.uuid4().hex[:8]
    raw_a, raw_b = generate_raw_key(), generate_raw_key()
    tenant_a_id = tenant_b_id = None

    try:
        async with app_factory() as s:
            tenant_a, _ = await create_tenant_with_key(
                s, name="TxPool Verify A", slug=f"txpool-verify-a-{suffix}", raw_key=raw_a
            )
            tenant_b, _ = await create_tenant_with_key(
                s, name="TxPool Verify B", slug=f"txpool-verify-b-{suffix}", raw_key=raw_b
            )
            await s.commit()
            tenant_a_id, tenant_b_id = tenant_a.id, tenant_b.id
        check("provisioned two temporary test tenants (A/B) over transaction pooler", True)

        # ── 1. resolve_key() end-to-end over the transaction pooler ─────────
        async with app_factory() as s:
            resolved = await resolve_key(raw_a, s)
            check(
                "resolve_key(valid key): SECURITY DEFINER bootstrap + "
                "schema-qualified ORM read, over transaction pooler",
                resolved is not None and resolved.id == tenant_a_id,
                f"got {resolved.id if resolved else None}, want {tenant_a_id}",
            )
        async with app_factory() as s:
            none_resolved = await resolve_key(generate_raw_key(), s)
            check(
                "resolve_key(invalid key) over transaction pooler returns None",
                none_resolved is None,
            )

        # ── 2. RLS cross-tenant isolation, re-proven with schema-qualified SQL ──
        async with app_factory() as s, s.begin():
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
            a_seen = await _count_visible(s, tenant_a_id)
            b_seen = await _count_visible(s, tenant_b_id)
            check("SELECT cross-tenant: A sees own row (1)", a_seen == 1, f"a_seen={a_seen}")
            check("SELECT cross-tenant: A sees 0 of B's rows", b_seen == 0, f"b_seen={b_seen}")

        async with app_factory() as s, s.begin():
            n = await s.scalar(text(f"SELECT COUNT(*) FROM {_TQ_API_KEYS}"))
            check(
                "no-context SELECT returns 0 rows (default-deny) over transaction pooler",
                int(n or 0) == 0,
                f"n={n}",
            )

        async with app_factory() as s, s.begin():
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
            rc = await _rowcount(
                s, f"UPDATE {_TQ_TENANTS} SET name = 'PWNED' WHERE id = :id", tenant_b_id
            )
            check("UPDATE cross-tenant: A updates 0 of B's rows", rc == 0, f"rowcount={rc}")

        async with app_factory() as s, s.begin():
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
            rc = await _rowcount(s, f"DELETE FROM {_TQ_TENANTS} WHERE id = :id", tenant_b_id)
            check("DELETE cross-tenant: A deletes 0 of B's rows", rc == 0, f"rowcount={rc}")

        insert_rejected = False
        try:
            async with app_factory() as s, s.begin():
                await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
                await s.execute(
                    text(
                        f"INSERT INTO {_TQ_API_KEYS} "
                        "(id, tenant_id, key_hash, key_prefix, is_active, created_at) "
                        "VALUES (:id, :tid, :kh, :pfx, true, now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tid": tenant_b_id,
                        "kh": "d" * 64,
                        "pfx": "txpoolab",
                    },
                )
        except (ProgrammingError, IntegrityError, DBAPIError) as exc:
            insert_rejected = "row-level security" in str(exc).lower()
        check(
            "INSERT cross-tenant: A-scoped session cannot stamp a row for B",
            insert_rejected,
            "WITH CHECK rejected the cross-tenant INSERT",
        )

        async with app_factory() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.bootstrap_key_hash', :kh, true)"),
                {"kh": hash_key(raw_a)},
            )
            total = await s.scalar(text(f"SELECT COUNT(*) FROM {_TQ_API_KEYS}"))
            a_via_boot = await _count_visible(s, tenant_a_id)
            b_via_boot = await _count_visible(s, tenant_b_id)
            check(
                "bootstrap GUC over transaction pooler reveals exactly the presented row",
                int(total or 0) == 1 and a_via_boot == 1 and b_via_boot == 0,
                f"total={total}, a={a_via_boot}, b={b_via_boot}",
            )
    finally:
        try:
            async with app_engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
                await conn.execute(
                    text(f"DELETE FROM {_TQ_API_KEYS} WHERE tenant_id = :tid"),
                    {"tid": tenant_a_id},
                )
                await conn.execute(text(f"DELETE FROM {_TQ_TENANTS} WHERE id = :id"), {"id": tenant_a_id})
            async with app_engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL app.current_tenant = '{tenant_b_id}'"))
                await conn.execute(
                    text(f"DELETE FROM {_TQ_API_KEYS} WHERE tenant_id = :tid"),
                    {"tid": tenant_b_id},
                )
                await conn.execute(text(f"DELETE FROM {_TQ_TENANTS} WHERE id = :id"), {"id": tenant_b_id})
            check("cleanup: both temporary test tenants (A/B) removed", True)
        except Exception as exc:  # noqa: BLE001 -- cleanup best-effort, still report
            check("cleanup completed", False, str(exc))
        await app_engine.dispose()

    print("\n" + "=" * 78)
    print("LIVE VERIFICATION — transaction pooler + schema-qualified isolation")
    print("=" * 78)
    all_ok = True
    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        all_ok = all_ok and ok
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {name.ljust(width)}"
        if detail:
            line += f"   ({detail})"
        print(line)
    print("=" * 78)
    print("RESULT:", "ALL CHECKS PASSED" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
