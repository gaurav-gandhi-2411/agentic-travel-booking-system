# ADR-0022 — [skip ci] Guardrail via Required Status Check

## Context

Issue #30 identified that automated bot commits (scheduled data updates, dependency refreshes) use `[skip ci]` in their commit messages. When a PR containing such commits is squash-merged, GitHub copies the full commit message into the squash-merge commit. This causes GitHub to suppress CI — including `Deploy-Staging` — on the merge commit, breaking the staging deploy pipeline.

The root constraint: **GitHub processes `[skip ci]` before any workflow runs.** There is no in-workflow mechanism to intercept or override this suppression. A workflow that tries to detect `[skip ci]` on its own trigger event will never run when the tag is present.

Two paths were evaluated:

**Path A (chosen): Workflow check + required branch protection**
- Add a `check-no-skip-ci` workflow that runs on `pull_request` events and scans all commits between the PR base and head for `[skip ci]` or `[ci skip]` (case-insensitive).
- Mark this check as required in branch protection for `main`.
- Effect: any PR whose commit history contains `[skip ci]` is blocked from merging until the offending commit is reworded.
- This fires on the PR source branch, not the merge commit — it runs before `[skip ci]` can propagate to main.

**Path C (rejected): Commit message rewrite at merge time**
- A bot rewrites the squash-merge commit message to strip `[skip ci]` after merge.
- Rejected: requires post-merge automation with write access to `main`, introduces a race condition between GitHub's CI suppression and the rewrite, and is fragile across GitHub UI flows (squash vs merge commit).

## Decision

Implement Path A: `check-no-skip-ci` workflow + required branch protection rule.

The workflow scans `git log origin/<base>..HEAD` for `[skip ci]` or `[ci skip]` (case-insensitive regex). If found, it fails with a descriptive message directing the author to reword the commit before merging.

## Consequences

**Required manual step (GG):** After this ADR's workflow merges to `main`, open:
> GitHub → Settings → Branches → Branch protection rules → Edit rule for `main` → "Require status checks to pass before merging" → add `check-no-skip-ci`

Without this branch protection setting, the workflow runs but does not block merges.

**Positive:**
- Eliminates the Deploy-Staging suppression root cause (Issue #30).
- Zero false positives for normal PRs that have no `[skip ci]` commits.
- Self-documenting failure message tells authors exactly what to fix.

**Negative:**
- Requires authors to reword commits when legitimate `[skip ci]` bot commits appear in a PR. This is intentional — `[skip ci]` bot commits should not appear in human PRs.
- Branch protection must be manually enabled (one-time, post-merge).

## Alternatives

See Path C above (rejected).

## References

- Issue #30 — [skip ci] suppresses Deploy-Staging on squash-merge
- Phase 2D iteration 1 Part B
