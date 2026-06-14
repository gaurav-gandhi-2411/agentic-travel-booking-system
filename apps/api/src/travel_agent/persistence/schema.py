from __future__ import annotations

# Single source of truth for the dedicated Postgres schema that DealHunter isolates
# ALL of its objects into: the tenants/api_keys tables, the resolve_api_key_secure
# function, every RLS policy, AND the Alembic version table.
#
# Why this exists: on the free tier we share an existing managed-Postgres instance with
# another project. Putting everything in a dedicated schema (NOT `public`) keeps our
# objects — and critically our Alembic migration history — completely separate from the
# co-tenant project's namespace. A shared `public.alembic_version` would corrupt both
# projects' histories; a dedicated `dealhunter.alembic_version` cannot.
#
# This constant is imported by env.py (migration config) and the runtime engine
# (connection search_path). The migrations themselves hardcode the literal "dealhunter"
# because a migration is an immutable snapshot and must not depend on a mutable import;
# the value here MUST match that literal.
DB_SCHEMA = "dealhunter"
