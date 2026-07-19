# GitHub Actions & ruleset traps (this repo)

Hard-won operational traps for any PR that touches `.github/workflows/*` or
interacts with the main-branch ruleset. Learned on
[#344](https://github.com/Gharib89/crm/issues/344) (ADR 0010 "build-once /
trust-PR" pipeline) and later incidents; each cost a real debugging cycle.

## 1. A new (or renamed) workflow does NOT run on the PR that introduces it

GitHub runs `pull_request` workflows only if they already exist on the
**default branch**. Empirically here, even a *modified registered* workflow
stayed silent when the PR touched only workflow files. So don't expect green
CI on the introducing PR — validate workflow changes via the local gate
(`actionlint` + `zizmor` — both run as pre-commit hooks on workflow edits —
plus pytest / pyright / `mkdocs build --strict` / packaging smoke), merge, then
exercise the workflow on the **first PR after it lands on main** (a throwaway
empty-commit PR works: `git commit --allow-empty` → open PR → watch checks →
close). Capture the exact matrix check-run names there (e.g.
`test (ubuntu-22.04)`) — the ruleset needs them.

## 2. The main ruleset must bypass admin, or semantic-release breaks

A ruleset that requires PRs / blocks direct pushes to main will break
`python-semantic-release`: PSR pushes its `chore(release): vX.Y.Z` commit
*directly* to main via `RELEASE_PAT`. Keep a `bypass_actors` entry for the
**Repository-admin role** (`{actor_id: 5, actor_type: "RepositoryRole",
bypass_mode: "always"}`) — `RELEASE_PAT` is admin-owned. Tag pushes
(`refs/tags/v*`) are not covered by a branch ruleset, so only the commit push
needs the bypass. The admin bypass also preserves the maintainer's
small-docs-commits-to-main workflow. The active ruleset is named
**"main: require PR + ci checks"** — confirm its current id/config via
`gh api repos/<o>/<r>/rulesets`.

## 3. Path-filtered checks as `required` can wedge a PR forever

`ci.yml` and `docs.yml` are both path-filtered. A PR whose **HEAD commit**
touches only paths outside **both** filters (e.g. CLAUDE.md / `.claude/`) gets
no required-check report on that SHA → combined status stays `pending` with
zero contexts → `mergeStateStatus: UNSTABLE` → merge stuck forever, even though
the same checks were green on earlier commits. Escape hatch — a deliberate
exception, not a default: `gh pr merge --squash --admin --delete-branch`, and
only after (a) the required checks passed on earlier commits of the same PR,
(b) the delta since is limited to paths no workflow builds (if it touches
`docs/**`/`mkdocs.yml`, `docs.yml` runs — wait for it instead), and (c) you ran
the matching local gate for the delta anyway (`mkdocs build --strict` for
anything docs-adjacent). Don't fake-trigger CI by touching `crm/**`. The real
fix would be dropping path-filtered checks from `required` (this trap's own
rule); until then, this is the documented workaround.

## 4. `[skip ci]` in the release commit message is a trap

The release tag points at the PSR commit — `[skip ci]` there would also
suppress the tag-triggered `release.yml`. Use a job-level
`if: !startsWith(github.event.head_commit.message, 'chore(release):')` guard
instead.

## 5. A push can silently fail to dispatch its `pull_request` workflows

A commit pushed to a PR branch can fire only the external checks (Copilot,
GitGuardian) while the GH-Actions workflows never start on that SHA — no run
appears in `gh run list` at all (absent, not cancelled). It's a GitHub event
hiccup, not config. **Re-fire with an empty commit**
(`git commit --allow-empty -m "ci: re-trigger" && git push`) — it emits
`synchronize`, path filters re-evaluate against the full PR diff, and it
collapses in the squash-merge. **Don't use close/reopen** — workflows whose
`on.pull_request.types` omit `reopened` (e.g. `bump-guard`) won't run.

Poll-loop caveat: right after a push the rollup can momentarily show only the
fast external check (`total=1, pending=0`) and falsely read "done" — guard on
`total >= <expected N>` AND `mergeStateStatus == CLEAN`.
