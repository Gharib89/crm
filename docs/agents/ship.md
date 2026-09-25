# Ship profile

Schema: 3

Every repo-specific fact `/ship` needs, one section per axis. Fourteen `##` headings, always present and in this order; a defaulted axis reads `None.` or `Default.` under its own heading. Facts sit on `Label:` lines and nowhere else, and the prose under a heading explains them. The `Schema:` line above is the profile schema `ship` checks at preflight; only a `setup-skills` re-run moves it. Vocabulary: the `ship` skill's source repo, `Gharib89/skills`, [CONTEXT.md](https://github.com/Gharib89/skills/blob/main/CONTEXT.md).

## Host

Host: github

## Worktree

Carry: None.
Bootstrap: None.

crm reads no `.env` and no credential env vars (credentials come only from a saved profile under `CRM_HOME`), so nothing gitignored needs carrying. Sibling worktrees have no `.venv`: the local gate resolves the main checkout's venv itself.

## Local gate

Location: scripts/local-gate.sh
Small node: a pytest node id, e.g. `crm/tests/test_entity_upsert_if_none_match.py::test_upsert_if_none_match_sets_header`; docs class: the path of the changed document, e.g. `docs/how-to/entity.md`, which runs `mkdocs build --strict`
Tripwires: None.

The gate uses the checkout's own `.venv`, else the main checkout's (found through `git rev-parse --git-common-dir`), with `PYTHONPATH` on the worktree; with neither it reports `deps` `unavailable` and names the install line. `actionlint` and `zizmor` run only when `.github/` changed; `actionlint` must be on `PATH` (v1.7.12, lockstep with CI).

## CI

Legs:
lint: ruff check, ruff format --check, pyright, semgrep house rules (1.169.0), actionlint (1.7.12), zizmor (1.26.1)
test: pytest with coverage on ubuntu-22.04 and windows-latest, plus the offline solution pack/extract e2e through the Power Platform CLI (pac)
package: PyInstaller build and smoke test of the bundle on ubuntu-22.04 and windows-latest
docs: mkdocs build --strict; path-filtered to docs/, mkdocs.yml, crm/, setup.py, CHANGELOG.md
bump-guard: the PR title implies no major bump unless the maintainer applied the `major` label
No-checks legal: yes; `docs` is path-filtered, though `ci.yml` and `bump-guard.yml` report on every PR to main
Push policy: Default.

`lint`, `test` and `package` come from `ci.yml`, `docs` from `docs.yml`, `bump-guard` from `bump-guard.yml`. Non-PR workflows: `e2e.yml` (live e2e, schedule and dispatch only), `semantic-release.yml` and `release.yml` (push to main and tag), and `docs.yml`'s Cloudflare deploy on push.

## Reviewers

### Copilot

Login: copilot-pull-request-reviewer[bot]
Trigger: auto-once
Request: None.
Workflow: None.
Cap: None.
Resolve: None.
Gating: no
Fallback-for: None.
Instructions: .github/copilot-instructions.md

The repo ruleset "Copilot auto review" fires one round on a ready PR, with `review_on_push: false`: that round is the only one, never re-requested. Its threads are dispositioned once.

### Claude Code

Login: claude[bot]
Trigger: on-request
Request: comment @claude
Workflow: .github/workflows/claude-review.yml
Cap: 2
Resolve: resolve-thread
Gating: no
Fallback-for: Copilot
Instructions: .github/copilot-instructions.md

Requested when Copilot exits degraded for any reason (never-queued, blocked, silent, infra-error, cap-hit, unreachable). Outside ship, any OWNER, MEMBER or COLLABORATOR `@claude` comment on a PR one of them authored also fires the workflow. CodeRabbit also reviews this repo on a `@coderabbitai review` comment, but it has no workflow for a round to be read off, which ship cannot express yet (Gharib89/skills#302), so it is not in this profile.

## Coding standards

docs/contributing/coding-standards.md

## Verification

### Live e2e

Proves: the changed command's requests and responses against a real D365 org, Dataverse online or on-prem v9.x
Applies when: the change touches a D365-touching command's Web API path (crm/core/*, or a command that calls the org), or fixes a bug reported on one target; not local/meta groups (profile, session, skill, self-update, repl, scaffold), docs or tooling
Run: `D365_E2E=1 D365_E2E_PROFILE=<profile> PYTHONPATH=<worktree> <venv>/bin/python -m pytest -m e2e crm/tests/e2e/<file> -k '<expr>'`, per the `live-e2e` skill
Needs: a saved crm profile for a live target: `agent-cloud` preferred, `agent-on-prem` needs the VPN; detect with `crm --profile <profile> connection whoami` exiting 0
Without it: hand-off
Also proven by CI: None.
Claims to probe: behavior that differs between on-prem v9.x and Dataverse online (entity set names, metadata attributes, platform permits); a target-specific bug is verified on the target it was reported on

On-prem is the priority target. `e2e.yml` runs the live suite on a schedule only, so no PR leg proves this.

### Solution packager e2e

Proves: `crm solution pack` and `extract` against the real Power Platform CLI
Applies when: the change touches solution pack/extract or its pac invocation
Run: `pytest -q -m e2e crm/tests/e2e/test_solution_packager_e2e.py`
Needs: `pac` on PATH; detect with `pac help` exiting 0
Without it: defer-to-ci
Also proven by CI: test
Claims to probe: pac CLI flags and output layout for the installed pac version

## Versioning and changelog

Tooling: semantic-release
Reads: the squash subject on main (the PR title)
In-PR requirement: None.
Subject constraints: Conventional Commit; `feat:` only for a new command, query mode or materially new capability, a small enhancement is `fix:` or `perf:`; a `!` or `BREAKING CHANGE:` needs the maintainer's `major` label (bump-guard fails otherwise); `chore(release):` is python-semantic-release's own

python-semantic-release bumps `setup.py` and `crm/__init__.py` and writes `CHANGELOG.md` at release; a PR never hand-edits any of the three.

## PR

Template: .github/PULL_REQUEST_TEMPLATE.md

## Public surface

Commands, flags, choices, defaults and exit codes; the `--json` output contract and `crm describe` schema; the shipped agent skill under `crm/skills/`; the saved-profile format under `CRM_HOME`; the PyInstaller bundle shape (its five sites: `crm.spec`, `release.yml`, `ci.yml` `package`, `scripts/build.sh`, `scripts/build.ps1`).

## Triage

File as an issue labelled `needs-triage`.

## Docs sync

Targets: README.md, docs/ (how-to/<group>.md, reference/cli.md), CONTEXT.md, docs/adr/, crm/skills/ (the shipped skill), crm/tests/TEST.md, crm/tests/e2e/DISCOVERED_BUGS.md, the e2e coverage gate (an `@covers` test or an `E2E_SKIP` entry in crm/tests/e2e/coverage.py)
Agent-facing: docs/agents/, .claude/skills/, crm/skills/, CLAUDE.md, .github/copilot-instructions.md

`CHANGELOG.md` is not a target: python-semantic-release owns it.

## Current docs

Sources: context7, Microsoft Learn
Pinned: click (>=8.4.1; relies on 8.4 internals, ADR 0025), pyright 1.1.411, ruff 0.15.21, semgrep 1.169.0, zizmor 1.26.1, actionlint 1.7.12, Dataverse Web API v9.2

## Cloud lane

PR cap: 3
Bootstrap: scripts/cloud-ship-bootstrap.sh
