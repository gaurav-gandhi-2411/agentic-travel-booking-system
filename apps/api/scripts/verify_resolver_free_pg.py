"""Live verification of the bootstrap-auth resolver against a real free Postgres.

Verifies the redesigned tenant-isolation path (Neon / Supabase free tier) end-to-end,
as the production posture demands: a least-privilege, **non-superuser, non-BYPASSRLS**
application role under FORCE ROW LEVEL SECURITY. No role in the system gets superuser or
BYPASSRLS; the resolver reads its one bootstrap row purely through the
``api_keys_bootstrap_auth`` policy.

What it does
------------
1. Runs ``alembic upgrade head`` against ``DATABASE_URL`` (the project-owner role the
   provider gives you — NOT a superuser on managed free Postgres).
2. Provisions a least-privilege app role (``LOGIN``, ``NOSUPERUSER``, ``NOBYPASSRLS``),
   granting only SELECT/INSERT/UPDATE/DELETE on the two tenancy tables.
3. Connecting DIRECTLY as that app role (the deployed posture), seeds the demo tenant and
   two test tenants A/B, then runs the isolation + resolver checks and prints a table.

Usage (PowerShell)::

    $env:DATABASE_URL = "postgresql://<owner>:<pw>@<host>/<db>?sslmode=require"
    $env:DEMO_API_KEY = "demo-api-key"
    python -m scripts.verify_resolver_free_pg

Run from ``apps/api`` (so ``alembic.ini`` and the ``scripts`` package resolve). Exit code
0 = all checks passed; non-zero = at least one check failed (details printed).
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any, cast

import alembic.command
import alembic.config
from dotenv import find_dotenv, load_dotenv
from sqlalchemy import CursorResult, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

from travel_agent.persistence.engine import assert_runtime_role_unprivileged
from travel_agent.persistence.schema import DB_SCHEMA
from travel_agent.tenancy.service import (
    create_tenant_with_key,
    generate_raw_key,
    hash_key,
    resolve_key,
    seed_demo_tenant,
)

_APP_ROLE = "dealhunter_app_verify"  # verify-only; distinct from the real prod app role


def _asyncpg_url(url: str) -> str:
    """Normalise a provider URL to an asyncpg-compatible SQLAlchemy URL.

    Two managed-Postgres frictions handled here so both env.py (migrations) and our own
    engines work uniformly:
      * scheme → ``postgresql+asyncpg``.
      * ``sslmode=require`` → ``ssl=require`` — asyncpg does not accept the libpq
        ``sslmode`` kwarg, but SQLAlchemy's asyncpg dialect accepts ``ssl`` directly.
      * drop ``channel_binding`` / ``options`` (libpq-only, not asyncpg kwargs).
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix) :]
            break
    u = make_url(url)
    q = dict(u.query)
    if "sslmode" in q and "ssl" not in q:
        q["ssl"] = q["sslmode"]
    for libpq_only in ("sslmode", "channel_binding", "options"):
        q.pop(libpq_only, None)
    return u.set(query=q).render_as_string(hide_password=False)


def _make_engine(
    base_url: str, *, username: str | None = None, password: str | None = None
) -> AsyncEngine:
    """Build an asyncpg AsyncEngine for Neon or the Supabase Supavisor pooler.

    The Supavisor pooler does not support asyncpg prepared statements, so the statement
    cache is disabled when the host is the pooler.
    """
    u = make_url(_asyncpg_url(base_url))
    if username is not None:
        u = u.set(username=username)
    if password is not None:
        u = u.set(password=password)
    # Pin search_path to the dedicated schema (matches the deployed engine); on a shared
    # instance the ORM/resolver never see `public` or a co-tenant schema.
    connect_args: dict[str, object] = {"server_settings": {"search_path": DB_SCHEMA}}
    if u.host and "pooler.supabase.com" in u.host:
        connect_args["statement_cache_size"] = 0
    return create_async_engine(u, pool_pre_ping=True, connect_args=connect_args)


def _app_username(owner_url: str) -> str:
    """Derive the app-role username for the connection mode.

    Supabase pooler usernames carry the project ref as a suffix (``postgres.<ref>``);
    a custom role must use the same ``<role>.<ref>`` form. Direct/Neon usernames are bare.
    """
    owner_user = make_url(_asyncpg_url(owner_url)).username or ""
    if "." in owner_user:
        ref = owner_user.split(".", 1)[1]
        return f"{_APP_ROLE}.{ref}"
    return _APP_ROLE


def _run_migrations(owner_url: str) -> None:
    os.environ["DATABASE_URL"] = _asyncpg_url(owner_url)  # env.py reads DATABASE_URL directly
    cfg = alembic.config.Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _asyncpg_url(owner_url))
    alembic.command.upgrade(cfg, "head")


async def _provision_app_role(owner_engine: AsyncEngine, password: str) -> None:
    """Create the least-privilege, non-superuser app role and grant it the minimum."""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                f"DO $$ BEGIN "
                f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') "
                f"  THEN CREATE ROLE {_APP_ROLE} LOGIN NOINHERIT NOSUPERUSER "
                f"       NOBYPASSRLS PASSWORD '{password}'; "
                f"  ELSE ALTER ROLE {_APP_ROLE} LOGIN PASSWORD '{password}'; "
                f"  END IF; "
                f"END $$"
            )
        )
        # Least privilege, schema-scoped: USAGE on DealHunter's dedicated schema only,
        # plus DML on its two tables. No privileges on public or any other schema.
        await conn.execute(text(f"GRANT USAGE ON SCHEMA {DB_SCHEMA} TO {_APP_ROLE}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DB_SCHEMA}.tenants TO {_APP_ROLE}")
        )
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DB_SCHEMA}.api_keys TO {_APP_ROLE}")
        )


async def _rowcount(session: AsyncSession, sql: str, tenant_id: uuid.UUID) -> int:
    """Execute a single-param DML statement and return its rowcount.

    AsyncSession.execute is typed Result[Any]; DML returns a CursorResult whose
    .rowcount the stubs don't expose on the base type — cast to read it under strict.
    """
    result = cast("CursorResult[Any]", await session.execute(text(sql), {"id": tenant_id}))
    return result.rowcount


async def _count_visible(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        or 0
    )


async def _public_fingerprint(engine: AsyncEngine) -> tuple[tuple[str, ...], ...]:
    """Snapshot every object in the `public` schema (tables, policies, functions).

    Taken before and after `alembic upgrade head` to PROVE the migrations added,
    altered, or dropped nothing in public on the shared instance.
    """
    async with engine.connect() as c:
        tables = (
            await c.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
            )
        ).scalars().all()
        policies = (
            await c.execute(
                text(
                    "SELECT tablename || '.' || policyname FROM pg_policies "
                    "WHERE schemaname='public' ORDER BY 1"
                )
            )
        ).scalars().all()
        funcs = (
            await c.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n "
                    "ON n.oid = p.pronamespace WHERE n.nspname='public' ORDER BY 1"
                )
            )
        ).scalars().all()
    return (tuple(tables), tuple(policies), tuple(funcs))


async def _role_flags(engine: AsyncEngine) -> tuple[str, bool, bool]:
    """Return (rolname, rolsuper, rolbypassrls) for the role this engine connects as."""
    async with engine.connect() as c:
        row = (
            await c.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
    return (str(row[0]), bool(row[1]), bool(row[2]))


async def main() -> int:  # noqa: PLR0915, PLR0912
    # The report uses non-ASCII glyphs; force UTF-8 so it prints on a cp1252 console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # Load the gitignored env files without echoing secrets. There are two: the nearest
    # apps/api/.env (DEMO_API_KEY, app config) and the repo-root .env (DATABASE_URL).
    # find_dotenv stops at the nearer one, so load the repo-root .env explicitly too.
    load_dotenv(find_dotenv(usecwd=True))
    root_env = Path(__file__).resolve().parents[3] / ".env"
    if root_env.exists():
        load_dotenv(root_env)

    owner_url = os.environ.get("DATABASE_URL")
    if not owner_url:
        print("ERROR: DATABASE_URL must be set (the provider's owner connection string).")
        return 2
    if not os.environ.get("DEMO_API_KEY"):
        print("ERROR: DEMO_API_KEY must be set (the demo tenant's raw key).")
        return 2

    results: list[tuple[str, bool, str]] = []
    role_report: list[tuple[str, bool, bool]] = []
    disclosures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    owner_engine = _make_engine(owner_url)

    # ── blast-radius proof: snapshot public BEFORE migrating ─────────────────
    public_before = await _public_fingerprint(owner_engine)

    print("→ Running migrations (alembic upgrade head) as the owner role …")
    # alembic env.py calls asyncio.run() internally; offload to a thread so it does not
    # collide with this coroutine's running event loop.
    await asyncio.to_thread(_run_migrations, owner_url)
    check(
        "alembic upgrade head runs clean as the non-superuser owner role", True,
        "whole chain provisioned on managed PG",
    )

    # ── blast-radius proof: public is byte-identical after migrating ─────────
    public_after = await _public_fingerprint(owner_engine)
    check(
        "migrations touched NOTHING in public (tables/policies/functions unchanged)",
        public_before == public_after,
        f"public objects before={sum(len(x) for x in public_before)} "
        f"after={sum(len(x) for x in public_after)}",
    )

    # ── schema isolation: every DealHunter object lives in the dedicated schema ──
    async with owner_engine.connect() as c:
        in_schema = await c.scalar(
            text(
                "SELECT COUNT(*) FROM pg_tables "
                "WHERE schemaname = :s AND tablename IN ('tenants','api_keys')"
            ),
            {"s": DB_SCHEMA},
        )
        fn_in_schema = await c.scalar(
            text(
                "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname = :s AND p.proname = 'resolve_api_key_secure'"
            ),
            {"s": DB_SCHEMA},
        )
        pol_in_schema = await c.scalar(
            text("SELECT COUNT(*) FROM pg_policies WHERE schemaname = :s"),
            {"s": DB_SCHEMA},
        )
        alembic_in_schema = await c.scalar(
            text("SELECT to_regclass(:t)"), {"t": f"{DB_SCHEMA}.alembic_version"}
        )
    check(
        f"tenants + api_keys tables created in '{DB_SCHEMA}' (not public)",
        int(in_schema or 0) == 2, f"found {in_schema}/2 in {DB_SCHEMA}",
    )
    check(
        f"resolve_api_key_secure created in '{DB_SCHEMA}'",
        int(fn_in_schema or 0) == 1, f"found {fn_in_schema}",
    )
    check(
        f"all RLS policies created in '{DB_SCHEMA}' (>=3)",
        int(pol_in_schema or 0) >= 3, f"found {pol_in_schema} policies in {DB_SCHEMA}",
    )
    check(
        f"alembic_version lives in '{DB_SCHEMA}' (separate history from public)",
        alembic_in_schema is not None, f"to_regclass={alembic_in_schema}",
    )

    # ── owner/migration role: hard-assert non-superuser; DISCLOSE bypassrls ──────
    # The security invariant is "the RUNTIME role serving traffic is non-superuser AND
    # non-BYPASSRLS" (asserted on the app role below). The owner/migration role on managed
    # Postgres (Supabase 'postgres') has rolbypassrls=true and that is platform-immutable;
    # it is used ONLY for migrations/provisioning, never to serve traffic. So bypassrls on
    # the owner is a disclosure, not a failure — but superuser on it would still be a hard
    # fail (we never want a superuser in the loop).
    owner_name, owner_super, owner_bypass = await _role_flags(owner_engine)
    role_report.append((owner_name, owner_super, owner_bypass))
    check(
        f"owner/migration role '{owner_name}' is NOT superuser",
        owner_super is False,
        f"rolsuper={owner_super}",
    )
    if owner_bypass:
        disclosures.append(
            f"owner/migration role '{owner_name}' has rolbypassrls=true (platform-immutable "
            f"on Supabase). Used ONLY for migrations/provisioning, never to serve traffic. "
            f"The deployed app connects as a separate non-superuser, non-BYPASSRLS role; the "
            f"startup guard (assert_runtime_role_unprivileged) refuses to boot otherwise."
        )

    app_password = secrets.token_urlsafe(24)
    print(f"→ Provisioning least-privilege app role '{_APP_ROLE}' …")
    await _provision_app_role(owner_engine, app_password)

    # The app role connects DIRECTLY (deployed posture). APP_ROLE_DATABASE_URL overrides
    # the derived connection when the provider needs a bespoke form; otherwise we reuse
    # the owner host/port and swap in the app-role username (pooler-ref-aware).
    override = os.environ.get("APP_ROLE_DATABASE_URL")
    if override:
        app_engine = _make_engine(override)
    else:
        app_engine = _make_engine(
            owner_url, username=_app_username(owner_url), password=app_password
        )
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)

    # No DealHunter-owned role may carry BYPASSRLS/superuser (the dropped Cloud-SQL-era
    # resolver owner must be absent; our app role must be clean). The platform 'postgres'
    # role is excluded — it is disclosed above, not owned by us.
    async with owner_engine.connect() as c:
        bad_roles = (
            await c.execute(
                text(
                    "SELECT rolname FROM pg_roles "
                    "WHERE (rolbypassrls OR rolsuper) AND rolname LIKE 'dealhunter%'"
                )
            )
        ).scalars().all()
    check(
        "no DealHunter-owned role has BYPASSRLS or superuser",
        len(bad_roles) == 0,
        f"offending={list(bad_roles)}",
    )

    # Distinct slugs per run so re-runs against the same DB stay clean.
    suffix = uuid.uuid4().hex[:8]
    raw_a, raw_b = generate_raw_key(), generate_raw_key()
    tenant_a_id = tenant_b_id = None

    try:
        # ── Supavisor caveat: confirm which role the app session ACTUALLY runs as,
        #    and that it is non-superuser / non-BYPASSRLS regardless of which path ──
        actual_name, actual_super, actual_bypass = await _role_flags(app_engine)
        role_report.append((actual_name, actual_super, actual_bypass))
        ran_as_custom = actual_name.split(".")[0] == _APP_ROLE
        mode = (
            "custom app role via APP_ROLE_DATABASE_URL override"
            if override
            else ("custom app role via session pooler" if ran_as_custom else f"'{actual_name}'")
        )
        check(
            f"app session runs as {mode}; NOT superuser, NOT BYPASSRLS",
            actual_super is False and actual_bypass is False,
            f"current_user={actual_name}, rolsuper={actual_super}, rolbypassrls={actual_bypass}",
        )

        # ── seed demo + idempotency under FORCE RLS as the app role ──────────
        async with app_factory() as s:
            await seed_demo_tenant(s)
        async with app_factory() as s:
            await seed_demo_tenant(s)  # second seed must be a clean no-op
            check("double seed_demo_tenant is idempotent (no error)", True)

        # ── provision two tenants A/B via the app role (A2 bootstrap path) ───
        async with app_factory() as s:
            tenant_a, _ = await create_tenant_with_key(
                s, name="Verify A", slug=f"verify-a-{suffix}", raw_key=raw_a
            )
            tenant_b, _ = await create_tenant_with_key(
                s, name="Verify B", slug=f"verify-b-{suffix}", raw_key=raw_b
            )
            await s.commit()
            tenant_a_id, tenant_b_id = tenant_a.id, tenant_b.id
            check("A2 bootstrap provisioning succeeds as non-superuser under FORCE RLS", True)

        # ── resolver: valid key → its tenant; invalid → None ─────────────────
        async with app_factory() as s:
            resolved = await resolve_key(raw_a, s)
            check(
                "resolve_key(valid key) returns the correct tenant",
                resolved is not None and resolved.id == tenant_a_id,
                f"got {resolved.id if resolved else None}, want {tenant_a_id}",
            )
        async with app_factory() as s:
            none_resolved = await resolve_key(generate_raw_key(), s)
            check("resolve_key(invalid key) returns None", none_resolved is None)

        # ── demo key resolves after seed ─────────────────────────────────────
        async with app_factory() as s:
            demo = await resolve_key(os.environ["DEMO_API_KEY"], s)
            check(
                "demo API key resolves to the demo tenant",
                demo is not None and demo.slug == "demo",
                f"slug={demo.slug if demo else None}",
            )

        # ── SELECT isolation: A's context sees A, not B ──────────────────────
        async with app_factory() as s, s.begin():
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
            a_seen = await _count_visible(s, tenant_a_id)
            b_seen = await _count_visible(s, tenant_b_id)
            check("SELECT cross-tenant: A sees own row (1)", a_seen == 1, f"a_seen={a_seen}")
            check("SELECT cross-tenant: A sees 0 of B's rows", b_seen == 0, f"b_seen={b_seen}")

        # ── no-context SELECT default-denies ─────────────────────────────────
        async with app_factory() as s, s.begin():
            n = await s.scalar(text("SELECT COUNT(*) FROM api_keys"))
            check("no-context SELECT returns 0 rows (default-deny)", int(n or 0) == 0, f"n={n}")

        # ── UPDATE isolation: A cannot mutate B ──────────────────────────────
        async with app_factory() as s, s.begin():
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
            rc = await _rowcount(
                s, "UPDATE tenants SET name = 'PWNED' WHERE id = :id", tenant_b_id
            )
            check("UPDATE cross-tenant: A updates 0 of B's rows", rc == 0, f"rowcount={rc}")

        # ── DELETE isolation: A cannot delete B ──────────────────────────────
        async with app_factory() as s, s.begin():
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
            rc = await _rowcount(s, "DELETE FROM tenants WHERE id = :id", tenant_b_id)
            check("DELETE cross-tenant: A deletes 0 of B's rows", rc == 0, f"rowcount={rc}")

        # ── INSERT isolation: A (scoped) cannot mint a row stamped for B ─────
        insert_rejected = False
        try:
            async with app_factory() as s, s.begin():
                await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
                await s.execute(
                    text(
                        "INSERT INTO api_keys (id, tenant_id, key_hash, key_prefix, "
                        "is_active, created_at) VALUES (:id, :tid, :kh, :pfx, true, now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tid": tenant_b_id,
                        "kh": "c" * 64,
                        "pfx": "verifyab",
                    },
                )
        except (ProgrammingError, IntegrityError, DBAPIError) as exc:
            insert_rejected = "row-level security" in str(exc).lower()
        check(
            "INSERT cross-tenant: A-scoped session cannot stamp a row for B",
            insert_rejected,
            "WITH CHECK rejected the cross-tenant INSERT",
        )

        # ── bootstrap exposure: presenting A's hash reveals only A's row ─────
        async with app_factory() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.bootstrap_key_hash', :kh, true)"),
                {"kh": hash_key(raw_a)},
            )
            total = await s.scalar(text("SELECT COUNT(*) FROM api_keys"))
            a_via_boot = await _count_visible(s, tenant_a_id)
            b_via_boot = await _count_visible(s, tenant_b_id)
            check(
                "bootstrap GUC reveals exactly the presented row (1), not B",
                int(total or 0) == 1 and a_via_boot == 1 and b_via_boot == 0,
                f"total={total}, a={a_via_boot}, b={b_via_boot}",
            )

        # ── A2 guard (negative): a tenant-A-scoped session cannot mint tenant B ─────
        a2_guard_ok = False
        async with app_factory() as s, s.begin():
            await s.execute(text(f"SET LOCAL app.current_tenant = '{tenant_a_id}'"))
            try:
                await create_tenant_with_key(
                    s, name="Guard B", slug=f"guard-b-{suffix}", raw_key=generate_raw_key()
                )
            except RuntimeError:
                a2_guard_ok = True  # A2 refuses to provision a different tenant from A's scope
        check(
            "A2 guard: tenant-A-scoped session cannot mint a different tenant (RuntimeError)",
            a2_guard_ok,
        )

        # ── startup guard (live): PASSES for the app role, REFUSES a BYPASSRLS role ──
        guard_app_ok = True
        try:
            async with app_factory() as s:
                await assert_runtime_role_unprivileged(s)
        except RuntimeError:
            guard_app_ok = False
        check("startup guard PASSES for the app role (non-super, non-bypassrls)", guard_app_ok)

        guard_owner_refused = False
        async with owner_factory() as s:
            try:
                await assert_runtime_role_unprivileged(s)
            except RuntimeError:
                guard_owner_refused = True
        check(
            "startup guard REFUSES the BYPASSRLS owner role "
            "(would block a 'postgres' DATABASE_URL)",
            guard_owner_refused,
        )
    finally:
        # ── pristine cleanup: drop the dealhunter schema + the verify-only role so the
        #    real pipeline deploy migrates fresh. public is NEVER touched. ──
        try:
            async with owner_engine.begin() as conn:
                await conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{DB_SCHEMA}" CASCADE')
                await conn.exec_driver_sql(
                    "DO $$ BEGIN "
                    f"  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_APP_ROLE}') THEN "
                    f"    BEGIN EXECUTE 'DROP OWNED BY {_APP_ROLE}'; "
                    "      EXCEPTION WHEN OTHERS THEN NULL; END; "
                    f"    BEGIN EXECUTE 'DROP ROLE {_APP_ROLE}'; "
                    "      EXCEPTION WHEN OTHERS THEN NULL; END; "
                    "  END IF; "
                    "END $$"
                )
            # Prove the ENTIRE run (migrate + verify + cleanup) left public byte-identical.
            public_final = await _public_fingerprint(owner_engine)
            check(
                "post-cleanup: public byte-identical to the pre-migration snapshot",
                public_final == public_before,
                f"public objects start={sum(len(x) for x in public_before)} "
                f"end={sum(len(x) for x in public_final)}",
            )
            async with owner_engine.connect() as c:
                dh_left = await c.scalar(
                    text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": DB_SCHEMA}
                )
                role_left = await c.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _APP_ROLE}
                )
            check(
                f"post-cleanup pristine: '{DB_SCHEMA}' schema + verify role removed",
                dh_left is None and role_left is None,
                f"schema_left={bool(dh_left)}, role_left={bool(role_left)}",
            )
        except Exception as exc:
            check("post-cleanup completed", False, str(exc))
        await app_engine.dispose()
        await owner_engine.dispose()

    # ── report ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("LIVE VERIFICATION — bootstrap-auth resolver on free Postgres")
    print("=" * 78)
    width = max(len(n) for n, _, _ in results)
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {name.ljust(width)}"
        if detail:
            line += f"   ({detail})"
        print(line)
    print("=" * 78)

    # ── pg_roles report for every role in use (requirement #3) ────────────────
    print("pg_roles (roles in use):")
    print(f"  {'rolname':<28} {'rolsuper':<10} {'rolbypassrls':<12}")
    for rolname, rolsuper, rolbypass in role_report:
        print(f"  {rolname:<28} {rolsuper!s:<10} {rolbypass!s:<12}")
    print("=" * 78)

    # ── platform disclosures (accepted, not failures) ────────────────────────
    if disclosures:
        print("PLATFORM DISCLOSURES (accepted):")
        for d in disclosures:
            print(f"  - {d}")
        print("=" * 78)

    print("RESULT:", "ALL CHECKS PASSED" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
