# Orchestrator Working Pattern

How GG and the Claude Code orchestrator collaborate on this repo. An external engineer picking up a session should read this alongside CURRENT_STATE.md.

## The spec-drop pattern

Every iteration starts with GG writing a `spec.md` at the repo root. The orchestrator's first move in a new session is always:

```bash
git add spec.md
git commit -m "chore: spec for <iteration name>"
git push
```

Then read `spec.md` and `CURRENT_STATE.md` in full before doing anything else. Orient via `git log --oneline -5`, `git branch -a`, `git status`. Report a 3-5 line orientation (what files are in scope, clean git state confirmation, plan for Part A / Part B) and **wait for "go"** before executing.

## Execution pattern

After "go":

1. **Executor subagent** for code changes. One executor per logical pass; each pass touches ≤6 files. Brief the executor with exact file paths, line numbers, what to change, and conventions to follow. Do not brief the executor on "based on your findings" — synthesize first, instruct precisely.

2. **Verifier subagent** after code. Runs: `pytest`, `ruff check`, `mypy src`, `pytest --cov=src --cov-fail-under=80`. Does not fix — only reports. The orchestrator reads the output and fixes any failures directly or via a targeted executor pass.

3. **Commit and PR** after all passes are green. Feature branch (`feat/<N>-<descriptor>`), not direct-to-main. Open PR, wait for CI, merge.

4. **CURRENT_STATE.md** updated last, in the same PR or a follow-up commit.

## Subagent roles

| Subagent | When to use |
|---|---|
| executor | Implement specific, well-scoped code changes. Exact file paths and instructions required. |
| verifier | Run verification commands (tests, lint, types, coverage). Does not write code. |
| Explore | Fast read-only search: find files, grep for symbols, answer "where is X defined." |
| Plan | Architecture/design decisions before implementing. |

## Standing conventions

These apply to every session without exception:

- **Never set `ANTHROPIC_API_KEY`** — any eval or test that would require a live Anthropic call is wrong. Tests mock judge responses.
- **No `[skip ci]` in commit messages** — there is an active required status check that blocks merges with `[skip ci]`. A commit with `[skip ci]` cannot merge to main.
- **No direct pushes to main** — always a feature branch + PR. The orchestrator does not bypass this even for "small" changes.
- **Zero production touch** during eval/CI-only iterations — confirmed at session start, guarded throughout.
- **No threshold changes** — `apps/api/evals/optimizer/thresholds.py` is a hard rule; changing thresholds to make failing evals pass is never acceptable.
- **Zero paid Anthropic spend** during eval iterations — tests use mocked judge responses; no live Sonnet calls.

## What needs GG approval

The orchestrator does not proceed on these without explicit confirmation:

| Action | Why |
|---|---|
| Production backend deploy (canary → full) | Two-phase gate: `workflow_dispatch stage=canary`, then GG smoke test, then `workflow_dispatch stage=full` |
| Production frontend deploy | `vercel deploy --prod --archive=tgz` — GG runs or confirms |
| History rewrites (git filter-repo) | Destructive; always back up first |
| Force push | Explicit refspecs only; never `--mirror` |
| New paid dependencies or paid API calls | Cost discipline — surface estimate before adding |
| Any change that alters existing baseline NUMBERS | Eval integrity — scores and thresholds are ground truth |
| Any action affecting shared infrastructure outside the repo | Secrets, Cloud Run permissions, Vercel project settings |

## What GG handles directly (not delegatable)

- Setting secrets in GitHub Actions, Vercel dashboard, or Google Secret Manager
- Manual smoke tests during the canary gate (the human-in-the-loop step)
- Vercel dashboard env var changes (Vercel CLI v54 preview-scope bug; dashboard is the only reliable path)
- Any OAuth / credential login flows
- Approving GitHub PR merges (the orchestrator opens PRs but does not merge without CI green)

## Escalation rules

Escalate (stop, explain, ask) before:

- Any change that would alter an existing baseline number (scores, thresholds, published metrics)
- A cache schema change that would invalidate the entire judge cache
- The cross-profile gate would crash the nightly cron (must degrade gracefully)
- A single executor pass would touch more than 6 files
- Any paid Sonnet/Anthropic judge call (should be zero per iteration)
- A destructive git operation with data-loss risk
- Genuine ambiguity in the spec that blocks forward progress

## Commit message conventions

[Conventional commits](https://www.conventionalcommits.org/):

```
feat(scope): what changed
fix(scope): what was wrong
docs(scope): documentation only
chore(scope): maintenance / tooling
style(scope): formatting only (ruff format, etc.)
test(scope): test-only changes
refactor(scope): no behavior change
```

Never include `Co-Authored-By: Claude` trailers. After every commit: `git log -1 --format=%B` — amend if a trailer slipped in.

## Cost discipline

- Log `/cost` at midpoint (after Part A) and close-out for any session with LLM calls.
- `usd_cost` is structured-logged per call in the eval framework.
- Eval iterations incur zero paid Anthropic spend — any spend in a session output means something went wrong.
- Paid re-baselines (e.g., all-Sonnet re-score of eval profiles) are explicitly on-demand only, triggered by GG, never by the orchestrator.

## How CURRENT_STATE.md is maintained

`CURRENT_STATE.md` is the primary handoff doc. The orchestrator updates it at the close of every iteration:

- Mark closed issues with strikethrough and resolution note
- Update test counts, coverage, and current revision
- Add a summary section for the iteration
- Update the "Production state" section if production changed
- Never delete historical audit context — append new sections

CURRENT_STATE.md is accurate as of the last closed iteration. The orchestrator at session start re-checks live state (git log, gh issue list, etc.) rather than trusting it blindly.
