# Contributing

## Setup

Follow the Getting Started instructions in `README.md`.

## Workflow

1. Branch from `main`: `git checkout -b feat/your-feature`.
2. Make changes in small, logical commits (conventional commit messages).
3. Run pre-flight before every commit:
   ```
   make lint && make typecheck && make test
   ```
4. Open a PR against `main`. CI must be green.

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):
`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.

Scope examples: `feat(api):`, `fix(web):`, `chore(infra):`.

## Code Standards

- **Python:** ruff (lint + format, 100-char lines), mypy strict, bandit.
- **TypeScript:** ESLint (next/core-web-vitals), tsc strict.
- **Test coverage target:** ≥ 80% for backend.
- **No secrets in code.** Use `.env.local` (gitignored). See `.env.example`.
- **ADR before code** for every load-bearing architectural decision.
  Add to `docs/architecture/adr/`.

## Adding Dependencies

- Python: add to `apps/api/pyproject.toml` `[project.dependencies]` or
  `[project.optional-dependencies].dev`, then `pip install -e "apps/api[dev]"`.
- Node: `cd apps/web && npm install <pkg>`.
- Run `pip-audit` / `npm audit` and resolve any high/critical findings before commit.
