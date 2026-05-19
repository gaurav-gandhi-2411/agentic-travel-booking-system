# Backlog

Lightweight list of deferred cleanup items. Not a sprint board — just a place to park things
that surfaced during active work but aren't worth a PR right now.

---

## BACK-001 — Use `find_dotenv()` for all .env loading in eval and dev scripts

**Surfaced:** 2026-05-17, Phase 2C.1 sanity test run
**Priority:** Low (cleanup)

Several scripts and ad-hoc dev commands use relative paths like `../.env` or `.env` to load
environment variables. These break silently depending on the working directory the script is
invoked from (e.g., running from `apps/api/` vs. repo root produces different results).

`find_dotenv()` from `python-dotenv` walks up the directory tree until it finds a `.env` file,
making load calls robust regardless of CWD.

**Scope:** Audit and update `apps/api/evals/optimizer/judge.py`, `runner.py`, `scorer.py`,
`evals/run.py`, and any ad-hoc dev scripts that call `load_dotenv()` with a hardcoded path.
Replace all occurrences with `load_dotenv(find_dotenv())`.

**Future PR:** Small standalone cleanup PR, no functional change.
