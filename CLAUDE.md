# crm — Project Memory

Python CLI for Microsoft Dynamics 365 Customer Engagement: on-prem v9.x (NTLM) or Dataverse online (OAuth client-credentials). Same commands hit both targets, over the Dataverse Web API (OData v4) / HTTPS. Single-package layout (`crm/`). pyright is strict by default (`pyrightconfig.json`); `crm/commands/*` and most tests opt out with a `# pyright: basic` header.

**On-prem is the priority target** — the maintainer's real, high-weight automation runs against on-prem. An on-prem-only capability (platform permits there, cloud blocks) is worth building on its on-prem value alone; don't down-rank it as niche because cloud/CI can't exercise it (precedent: ADR 0013).

This repo is public on GitHub, so keep client and org identifiers (real org names, internal hostnames, GUIDs from a live org) out of commits, PR text and issues; the placeholders are `Contoso` / `internalcrm.contoso.local`. The `secret_scan_gate.py` hook scans `git commit` content only, so PR bodies, issues and comments are on you.

Claude sessions wrote most of this codebase, and agents file and triage its tickets. Treat existing structure as earlier agent work, not as a human's design to preserve: when your change would add a second copy of existing logic, consolidate it in the same diff and name it in the PR body. A ticket's premise and proposed wording are an agent's claims to measure; its acceptance criteria still bound the diff. Design forks go to the owner.

## Credential model

Setup is `crm profile add` (interactive wizard on a TTY; flag-driven for scripting): it infers auth from the URL, saves the secret (OS keyring, else `0600` plaintext on WSL/headless), tests via WhoAmI, and activates. `crm profile --help` lists the other profile verbs; diagnostics live only under `crm connection`. The CLI reads no `.env` and no credential env vars (`D365_*`, `CRM_USERNAME`, `CRM_PASSWORD` and the like): credentials come only from a saved profile or `--password` (per-run override); secret resolution is `--password` > stored plaintext > keyring > TTY prompt. `CRM_HOME` is the only env var in credential resolution (the state home; unset or empty means `~/.crm`); other `CRM_*` vars change behaviour, some of them what a write does on the server (`CRM_AS_USER*` impersonates, `CRM_BYPASS_PLUGINS`, `CRM_SUPPRESS_DUP`): `rg 'environ' crm` lists them. Two readers of env are not the CLI: the e2e fixture seeds a throwaway profile from `D365_E2E_PROFILE` or flat `D365_*`, and `scripts/windows-vm.sh` reads `WINVM_*` from the main checkout's gitignored `.env`. A connection command with no profile auto-launches `crm profile add` on a TTY; under `--json`/no-TTY it errors with "run `crm profile add`".

## Architecture

- Auth lives in `crm/utils/d365_backend.py` and `crm/utils/adfs.py`; credential resolution (`resolve_credentials`) in `crm/core/connection.py`; profile inference (`infer_auth_scheme`) in `crm/commands/profile.py`.
- `docs/research/` and `docs/superpowers/` are archived history: excluded from the site build (`exclude_docs`) and, through the root `.ignore`, from `rg` and the Grep tool; `git grep` needs the pathspec `-- ':!docs/research' ':!docs/superpowers'`. Caller searches also exclude `CHANGELOG.md`: `rg -g '!CHANGELOG.md'`, or `':!CHANGELOG.md'` in `git grep`.

Coding standards: `docs/contributing/coding-standards.md` is canonical — every reviewer (the `code-review` skill's Standards axis, `.coderabbit.yaml` path instructions, `.github/copilot-instructions.md`) derives from it. Rule changes land there first, then re-derive the reviewer configs.

## Branch & worktree discipline

The main checkout (`~/wip/projects/crm`) is shared by concurrent agent sessions: do not develop in it directly. Any feature or bug fix happens in a git worktree on a fresh branch (under Claude Code the `destructive_op_gate.py` hook also blocks a non-docs commit on `main` here):

1. `git worktree add ../crm.worktrees/<slug> -b <type>/<topic> main` (a sibling of the checkout; attended `/ship` puts its own under `.claude/worktrees/` and a T3 thread under `~/.t3/worktrees/crm/`, so `git worktree list` is the way to find one).
2. All work, commits, and the PR happen from that branch.
3. Remove the worktree after merge.

A worktree's own `.venv` is optional: the strict pyright hook, `scripts/check.sh` and `scripts/local-gate.sh` each use it when present, else the main checkout's; for an ad-hoc run use `PYTHONPATH=$WT <main-venv>/bin/python -m pytest` (the main venv's editable install points at the main checkout otherwise). In the shared checkout itself: read-only work and small docs-only commits to `main` (via a throwaway worktree if the dir is on someone else's branch). Before any git mutation anywhere: `git branch --show-current && git status -sb` first, and stage with explicit paths, not `git add -A`, `--all` or `.` (the same hook blocks them).

## Commands

```bash
pip install -e ".[dev,docs]"              # dev + docs deps
pytest                                    # offline suite; addopts pins `-m 'not e2e'`, so e2e is skipped by default
pytest crm/tests/test_query_count.py::test_x    # single test (or `-k '<expr>'` to match by name); `-m slow` for the slow ops
npx --yes --package=pyright@1.1.414 pyright --pythonpath .venv/bin/python --pythonversion 3.13   # local lint (omit pythonpath → ~56 false errors); one npm pin across CI, gate and hooks (setup.py [dev] comment); strict + py3.13 pinned in pyrightconfig.json
ruff check . && ruff format --check .     # lint + format gate; CI runs both (config in pyproject.toml)
uvx semgrep==1.169.0 scan --config ci/semgrep-rules.yml --error --metrics off   # house-convention rules (dry-run contract); CI `lint` runs the same pin in its own venv. Not in [dev]: engine needs py>=3.10
uvx zizmor==1.26.1 .                       # GitHub Actions security audit; CI `lint` runs it pinned in its own venv (config `.github/zizmor.yml`, scoped unpinned-uses policy). Also a pre-commit hook; version lockstep across both. Not in [dev]
pre-commit install                        # once per clone: ruff + codespell run on staged files at commit time (actionlint + zizmor on workflow edits)
sudo apt install shellcheck               # once per machine (or brew): the shellcheck hook runs the ShellCheck on PATH, not a pinned copy
mkdocs build --strict                     # docs; CI runs this, warnings fail
```

## Driving the CLI from bash or zsh (output-capture traps)

The Bash tool runs your login shell (`$SHELL`), zsh on the maintainer's machine (`echo $0` to check), so the bash and the zsh quirks below can each apply. Run checkout code through `.venv/bin/crm`, or `PYTHONPATH=$WT <main-venv>/bin/python -m crm` in a worktree: `crm` on `PATH` is the installed release binary, which lags HEAD. The `crm` and `crm-migration` skills a local session loads are likewise the installed copies (`~/.claude/skills/<name>/`, written by `crm skill install` and refreshed by `crm self-update`), not `crm/skills/` or `crm/migration-skill/`: read the checkout's files when the skill text matters. Three quirks silently fake results when you capture `crm` output (in e2e `cli`-fixture checks, QA sweeps, or any scripted run), and each one reads like a CLI bug when it's really the harness lying:

- **Pass args as an array, expanded `"${P[@]}"`.** A string `P="--profile x"; crm $P …` passes `--profile x` as a *single* arg in zsh → `No such option '--profile x'`. An array expanded bare as `$P` passes only its first element in bash, so `crm $P metadata …` reads `metadata` as the profile name. `P=(--profile x); crm "${P[@]}" …` works in both shells.
- **`| head` / SIGPIPE corrupts the captured exit code** (either shell). A real exit-0 can surface as non-zero when `head` closes the pipe early (1 or 120, it varies run to run, and it can also stay 0). Assert exit codes with **no pipe**: `crm … >/dev/null 2>&1; echo $?`.
- **MULTIOS tees redirections (zsh only).** `crm … 2>&1 1>/dev/null | wc` shows the same output on *both* streams, faking a stdout/stderr duplication. Check stream separation with **files**, not pipes: `crm … >/tmp/o 2>/tmp/e; diff /tmp/o /tmp/e`.

Robust pattern: pass args via an array (`"${P[@]}"`), run to temp files (no pipe), capture `$?` immediately, then `head`/`grep`/`diff` the **files**. Treat any pipe-, `head`-, or `2>&1`-based finding as suspect until reproduced without the pipe.

## Keep docs in sync with code

Every feature / new command / flag / behavior change ships its docs in the **same** change:

- **README.md** — user-facing capability or install change.
- **CHANGELOG.md**: do not hand-edit. `python-semantic-release` owns it (see **Release** below), and under Claude Code the `block_protected_files.py` hook blocks an Edit or Write. Ship a good `fix:`/`feat:` commit subject instead; for a squash-merge, set the squash *subject* to that line so PSR bumps and documents correctly. There is no `## [Unreleased]` section to maintain.
- **docs/** — matching `docs/how-to/<group>.md` and `docs/reference/cli.md`.
- **SKILL ↔ CLI** — two shipped agent skills, each its own source of truth: `crm/skills/` (`crm`) and `crm/migration-skill/` (`crm-migration`, ADR 0033), each a thin `SKILL.md` router + `reference/*.md`. `crm skill install [--skill <name>]` copies a tree into a harness dir outside the repo (`~/.claude/skills/<name>/`, etc.). Rules:
    - **Self-contained** — the skill ships to users who have only the skill, not the repo; never link a shipped skill file to a repo path (`docs/**`, `GLOSSARY.md`) — inline what's needed.
    - **Never restate flags/choices/defaults** — the skill states only what `crm describe`/`--help` cannot (workflows, gotchas, the JSON contract).
    - **Never track an in-repo copy** of the **crm** skill; source of truth is `crm/skills/`.
    - The tracked `.claude/skills/` tree holds three kinds of skill, and only the last is hand-edited. **Lock-recorded** derived copies are the keys of `skills-lock.json`; refresh one by re-running its install line (see "Ship" below). **Vendored** ones (the interactive toolkit) are copies of personal skills: `python scripts/sync-skills.py` rewrites them from `~/.claude/skills/` and stamps `metadata.internal: true` on each, and its docstring holds the rest (dependency closure, what it refuses to touch). **Project-native** ones (`merge-gate`, `live-e2e`, `audit-crm-skill`) have `.claude/skills/` as their source of truth and get the internal flag by hand. `crm/tests/test_skill_bundle.py` fails a vendored or project-native `SKILL.md` without the flag; lock-recorded copies are exempt. `.claude/skills/harness-pyright-lsp/` is none of the three: it is a Claude Code plugin that `/setup-harness` vendors (its `README.md` says why), refreshed by re-running `/setup-harness`.
    - Any session or agent that edits a skill (a shipped skill tree such as `crm/skills/`, or a project-native skill under `.claude/skills/`) invokes the **`writing-for-agents`** skill (a lock-recorded copy at `.claude/skills/writing-for-agents/`) through the Skill tool before committing, as the authority for skill structure and description rules, and names that pass in the PR body.
    - See `docs/contributing/skill-and-cli.md`.
- **E2E coverage gate**: every new/changed D365-touching command ships a live e2e test under `crm/tests/e2e/` stamped `@covers("<group> <verb>")`, or an `E2E_SKIP` entry with a reason in `crm/tests/e2e/coverage.py`; the offline gate (`crm/tests/test_e2e_coverage_gate.py`) fails CI otherwise. Out of scope: the local/meta groups in `LOCAL_GROUPS` and the offline verbs of an org-touching group in `LOCAL_VERBS` (e.g. `inventory diff`), both sets in that same file. See `crm/tests/TEST.md`.
- **Test classification docs** — a capability-gate change (`@requires_cloud` / `@requires_onprem` added or removed on an e2e test) must update the live-run table in `crm/tests/TEST.md`; fixing or reclassifying a defect tracked in `crm/tests/e2e/DISCOVERED_BUGS.md` must update that entry in the same change.

### Running the live e2e suite

Live e2e runs (`D365_E2E=1 pytest -m e2e`) and any live-org verification follow the `live-e2e` skill (`.claude/skills/live-e2e/SKILL.md`). Pin `--profile <name>` on any live command and confirm the org via `crm connection whoami` before reporting target-specific facts.

`.github/workflows/docs.yml` runs `mkdocs build --strict` on code and docs changes (its `paths:` filter), so stale refs and broken links fail CI.

## Release

Releases are cut automatically by `python-semantic-release` (`.github/workflows/semantic-release.yml`, config in `pyproject.toml` `[tool.semantic_release]`). Every push to `main` reads the Conventional Commit history since the last tag, bumps the version in both `setup.py` and `crm/__init__.py`, updates `CHANGELOG.md`, commits `chore(release): vX.Y.Z`, and pushes tag `vX.Y.Z`. So commit messages drive the bump: `feat:`→minor, `fix:`/`perf:`→patch, breaking (`!`/`BREAKING CHANGE:`)→major.

**Bump discipline — reserve `feat:` for substantial new capability; minor is not the default.** A small enhancement (a new flag/alias on an existing command, a tweak, a polish) ships as `fix:` or `perf:` → **patch** bump. Use `feat:` only for a genuinely new command, a new query mode, or a materially new capability → minor bump. The minor digit tracks real features; the patch digit absorbs the steady stream of small improvements.

The tag is pushed with `RELEASE_PAT`, since a tag pushed with `GITHUB_TOKEN` fires no downstream workflow (the `semantic-release.yml` header has the detail). The tag fires `release.yml`, which builds the PyInstaller binaries, uploads to R2, and creates the GitHub release. `scripts/check_tag_version.py` gates that the tag matches `setup.py`.

Manual release (fallback / re-cut): bump both version files, then push the tag yourself (a human/PAT tag push fires `release.yml`).

Any PyInstaller bundle-shape change touches all five sites: `crm.spec`, `.github/workflows/release.yml`, `.github/workflows/ci.yml` (the `package` job), `scripts/build.sh` and `scripts/build.ps1`.

## Agent skills

### Harness

`scripts/check.sh` is this repo's check entry point: `edit <file>...` lints and formats, `turn` typechecks and runs the affected tests of every uncommitted change, and `full` answers for the whole repo: the runner on every file, `mkdocs build --strict` and `semgrep scan --config ci/semgrep-rules.yml`. It prints one JSON line and exits 0 pass, 1 fail, 2 unavailable, 3 over budget. Hooks in `.claude/settings.json` run `edit` after every Edit or Write and `turn` at every stop, and the pre-commit runner is the commit rung. The same file denies the Read tool on `.env` and `.env.*` except `.env.example` in every session, cloud included; Bash reads are not covered. Harness profile: `docs/agents/harness.md`. Re-run `/setup-harness` after adding a stack, a member or a tool.

Where the `LSP` tool answers (a cloud session starts no language server), use its `goToDefinition` rather than grep to find where a symbol lives, and before changing a function's signature or renaming a symbol, call `findReferences` on it and cross-check the list with a grep for the name. A session's first `findReferences` can come back incomplete while the server is still indexing, often as only the declaration, so run it twice and use the second answer. Trust neither source alone: a file changed through Bash stays invisible to the server for the rest of the session, and grep misses aliased imports and attribute access. The server roots at the folder the session was in at its first `LSP` call and never moves, so in a worktree the session reached by path from the main checkout (a Bash `cd`), `LSP` answers and diagnostics read the main checkout's code: use grep there.

### Subagents

A lookup whose target you know is a direct `LSP` or grep call. A sweep across many files goes to the **`Explore`** agent on the **haiku** model.

### Issue tracker

Issues live in GitHub Issues at `Gharib89/crm`. Use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Code review

`/ship`'s review loop runs the reviewers in `docs/agents/ship.md` `## Reviewers`, which holds each one's cap, trigger and exit rule: Claude Code first, with a Codex fallback. CodeRabbit is manual and merge-gate only.

- **Claude Code** (`.github/workflows/claude-review.yml`) runs on an `@claude` PR comment: any OWNER, MEMBER or COLLABORATOR comment containing it, on a PR one of them authored, fires a round. House rules for it live in `.github/copilot-instructions.md`.
- **Codex** is the fallback, requested with a `@codex review` PR comment. It reads the `Review guidelines` section below through `AGENTS.md`. Anywhere but the request itself, write the phrase without its `@` (`codex review`): every mention starts a round or a Codex task.
- **Copilot** does not review this repo, and no Copilot ruleset exists.
- **CodeRabbit** does not auto-review this repo (star-gated) and is not in the ship profile. The `merge-gate` skill drives it: `@coderabbitai review` while the PR is open (a closed PR declines), after each fix push; `@coderabbitai resolve` only once every thread carries a disposition. Config in `.coderabbit.yaml`.

#### Review guidelines

For Codex PR review, which reads this section through `AGENTS.md`. Review against `docs/contributing/coding-standards.md` and `.github/copilot-instructions.md`. Review only what this repo controls. Skip the derived skill copies under `.claude/skills/`: every lock-recorded skill (a key of `skills-lock.json`), every vendored copy synced by `scripts/sync-skills.py`, and the `harness-pyright-lsp` plugin. A defect in one of them is fixed at its source, not here. Skip `skills-lock.json` and `CHANGELOG.md` as well. Under `.claude/skills/`, only the project-native `merge-gate`, `live-e2e` and `audit-crm-skill` are in scope.

### PR merge gate

Inbound agent-shipped PRs (cloud-ship routine, codex, teammates' agents) get a second, local review pass via the `merge-gate` skill before the maintainer merges, ending in a `gate-passed`/`gate-failed` label. See `docs/agents/pr-merge-gate.md`.

### Domain docs

Single-context — `GLOSSARY.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.

### Ship

`/ship` drives one issue to a merge-ready PR. This repo's ship profile: `docs/agents/ship.md`. Without that file ship refuses: run `/setup-skills`.

Every skill `skills-lock.json` lists is a derived copy, changed at its source and refreshed here; `skills-lock.json` records each one's source. `ship`, `cloud-ship`, `setup-skills`, `update-skills` and any other skill the lock records from `Gharib89/skills` come from it; the skills ship and setup-skills compose come from `mattpocock/skills`, `upstash/context7` and `humanlayer/skills`, each at the pin of the skill that composes it. `/update-skills` refreshes them all in one PR. By hand, refresh a skill by re-running its install line at project scope, without `-g`. Ship's refresh chains its preflight, so a profile the refreshed ship no longer reads is reported now, not on the next `/ship`: `flags=$(jq -er '[.skills | to_entries[] | select(.value.source | ascii_downcase == "gharib89/skills") | .key] + ["ship", "cloud-ship", "setup-skills", "update-skills"] | unique | map("--skill " + @sh) | join(" ")' skills-lock.json) && eval "npx skills add Gharib89/skills $flags --agent claude-code -y" && .claude/skills/ship/scripts/preflight.sh none`.
