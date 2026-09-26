# crm — Project Memory

Python CLI for Microsoft Dynamics 365 Customer Engagement — on-prem v9.x (NTLM) **or** Dataverse online (OAuth client-credentials). Same commands hit both targets, over the Dataverse Web API (OData v4) / HTTPS. Single-package layout (`crm/`), pyright strict on `crm/core/*` and `crm/utils/d365_backend.py`, basic mode elsewhere.

**On-prem is the priority target** — the maintainer's real, high-weight automation runs against on-prem. An on-prem-only capability (platform permits there, cloud blocks) is worth building on its on-prem value alone; don't down-rank it as niche because cloud/CI can't exercise it (precedent: ADR 0013).

## Credential model

Setup is `crm profile add` (interactive wizard on a TTY; flag-driven for scripting) — infers auth from the URL, saves the secret (OS keyring → `0600` plaintext fallback on WSL/headless), tests via WhoAmI, and activates. Profile verbs: `crm profile add | use | list | edit | rm | set-password | delete-password`. Diagnostics only under `crm connection whoami | test | doctor | status`. **No `.env`, no credential env vars** (`D365_*`/`CRM_*`/`D365_AUTH`/`CRM_AUTH_SCHEME` are not read) — credentials come only from a saved profile or `--password` (per-run override); secret resolution is `--password` > stored plaintext > keyring > TTY prompt. The only retained env knob is `CRM_HOME` (state dir). A connection command with no profile auto-launches `crm profile add` on a TTY; under `--json`/no-TTY it errors with "run `crm profile add`".

## Architecture

- `crm/core/*` — Web API logic, one module per domain (`entity`, `query`, `metadata`, `solution`, …); pyright **strict**.
- `crm/commands/*` — thin Click wrappers, one per `crm <group>`; `crm/cli.py` wires them; `crm/__main__.py` is the entry.
- `crm/skills/` — agent skill shipped in the wheel: a thin `SKILL.md` router + `reference/*.md` loaded on demand (kept in sync with the CLI — see below).

Coding standards: `docs/contributing/coding-standards.md` is canonical — every reviewer (the `code-review` skill's Standards axis, `.coderabbit.yaml` path instructions, `.github/copilot-instructions.md`) derives from it. Rule changes land there first, then re-derive the reviewer configs.

## Branch & worktree discipline

The main checkout (`~/wip/projects/crm`) is shared by concurrent agent sessions — **never develop in it directly**. Any feature or bug fix happens in a **git worktree on a fresh branch**:

1. `git worktree add ../crm.worktrees/<slug> -b <type>/<topic> main` (a sibling of the checkout, where `/ship` puts its own worktrees).
2. All work, commits, and the PR happen from that branch.
3. Remove the worktree after merge.

Worktrees have no `.venv`: `scripts/local-gate.sh` finds the main checkout's venv itself; for an ad-hoc run use `PYTHONPATH=$WT <main-venv>/bin/python -m pytest` (the main venv's editable install points at the main checkout otherwise). In the shared checkout itself: read-only work and small docs-only commits to `main` (via a throwaway worktree if the dir is on someone else's branch). Before **any** git mutation anywhere: `git branch --show-current && git status -sb` first, and stage with explicit paths, never `git add -A`.

## Commands

```bash
pip install -e ".[dev,docs]"              # dev + docs deps
pytest                                    # offline suite; addopts pins `-m 'not e2e'`, so e2e is skipped by default
pytest crm/tests/test_query.py::test_x    # single test (or `-k '<expr>'` to match by name); `-m slow` for the slow ops
pyright --pythonpath .venv/bin/python --pythonversion 3.13   # local lint (omit pythonpath → ~56 false errors); strict + py3.13 pinned in pyrightconfig.json
ruff check . && ruff format --check .     # lint + format gate; CI runs both (config in pyproject.toml)
uvx semgrep scan --config ci/semgrep-rules.yml --error --metrics off   # house-convention rules (dry-run contract); CI `lint` runs this in its own venv. Not in [dev] — engine needs py>=3.10; `pipx install semgrep` or `uvx semgrep`
uvx zizmor==1.26.1 .                       # GitHub Actions security audit; CI `lint` runs it pinned in its own venv (config `.github/zizmor.yml`, scoped unpinned-uses policy). Also a pre-commit hook; version lockstep across both. Not in [dev]
pre-commit install                        # once per clone: ruff + codespell run on staged files at commit time (actionlint + zizmor on workflow edits)
mkdocs build --strict                     # docs; CI runs this, warnings fail
```

## Driving the CLI from zsh (output-capture traps)

The shell here is **zsh**. Three quirks silently fake results when you capture `crm` output — in e2e `cli`-fixture checks, QA sweeps, or any scripted run — and each one reads like a CLI bug when it's really the harness lying:

- **No word-split on unquoted vars.** `P="--profile x"; crm $P …` passes `--profile x` as a *single* arg → `No such option '--profile x'`. Use a zsh **array** `P=(--profile x)` (or `${=P}`), never a plain string.
- **`| head` / SIGPIPE corrupts the captured exit code** (not zsh-specific). A real exit-0 can surface as exit-1 when `head` closes the pipe early and Click catches `BrokenPipeError`. Assert exit codes with **no pipe**: `crm … >/dev/null 2>&1; echo $?`.
- **MULTIOS tees redirections.** `crm … 2>&1 1>/dev/null | wc` shows the same output on *both* streams, faking a stdout/stderr duplication. Check stream separation with **files**, not pipes: `crm … >/tmp/o 2>/tmp/e; diff /tmp/o /tmp/e`.

Robust pattern: pass args via an array, run to temp files (no pipe), capture `$?` immediately, then `head`/`grep`/`diff` the **files**. Treat any pipe-, `head`-, or `2>&1`-based finding as suspect until reproduced without the pipe.

## Keep docs in sync with code

Every feature / new command / flag / behavior change ships its docs in the **same** change:

- **README.md** — user-facing capability or install change.
- **CHANGELOG.md** — do **not** hand-edit. `python-semantic-release` owns it: it generates each version's section from the Conventional Commit history at release time (see **Release** below). Ship a good `fix:`/`feat:` commit subject instead; for a squash-merge, set the squash *subject* to that line so PSR bumps and documents correctly. There is no `## [Unreleased]` section to maintain.
- **docs/** — matching `docs/how-to/<group>.md` and `docs/reference/cli.md`.
- **SKILL ↔ CLI** — `crm/skills/` is the single tracked agent skill (source of truth): a thin `SKILL.md` router + `reference/*.md`. `crm skill install` copies the whole tree into a harness dir outside the repo (`~/.claude/skills/crm/`, etc.). Rules:
    - **Self-contained** — the skill ships to users who have only the skill, not the repo; never link a shipped skill file to a repo path (`docs/**`, `CONTEXT.md`) — inline what's needed.
    - **Never restate flags/choices/defaults** — the skill states only what `crm describe`/`--help` cannot (workflows, gotchas, the JSON contract).
    - **Never track an in-repo copy** of the **crm** skill; source of truth is `crm/skills/`.
    - The tracked `.claude/skills/` tree holds three kinds of skill, none hand-edited except the last. **Lock-recorded derived** copies (`ship`, `cloud-ship`, `setup-skills`, `update-skills` and the skills ship composes: `tdd`, `code-review`, `writing-for-agents`, `triage`, `find-docs`, `show-me`) are installed by the skills CLI and recorded in `skills-lock.json`; refresh one by re-running its install line (see "Ship" below). **Vendored** ones (the interactive toolkit) are *derived* copies of personal skills whose source of truth is `~/.claude/skills/`; run `python scripts/sync-skills.py` to refresh them and commit the result — **never hand-edit a vendored copy** (the next sync overwrites it). The tool copies each listed skill verbatim, stamps `metadata.internal: true` on every copy (so `npx skills add Gharib89/crm` hides these dev skills from end users — revealed only with `INSTALL_INTERNAL_SKILLS=1`), transitively pulls each skill's dependencies, and refuses to touch a lock-recorded or project-native skill. **Project-native** ones (`merge-gate`, `live-e2e`, `audit-crm-skill`) have no *separate* source tree — `.claude/skills/` is itself their source of truth, hand-edited here. Every vendored and project-native `SKILL.md` must carry the internal flag (project-native skills get it hand-added); lock-recorded copies stay verbatim and are exempt. A regression test in `crm/tests/test_skill_bundle.py` enforces both.
    - When editing `crm/skills/`, the docs-sync agent invokes the **`writing-for-agents`** skill (a lock-recorded copy at `.claude/skills/writing-for-agents/`) as the authority for skill structure and description rules.
    - See `docs/contributing/skill-and-cli.md`.
- **E2E coverage gate** — every new/changed D365-touching command must ship a live e2e test under `crm/tests/e2e/` stamped `@covers("<group> <verb>")`, **or** an `E2E_SKIP` entry with a reason in `crm/tests/e2e/coverage.py`. The offline gate (`crm/tests/test_e2e_coverage_gate.py`) fails CI otherwise. Local/meta groups (`profile`, `session`, `skill`, `self-update`, `repl`, `scaffold`) are out of scope (`LOCAL_GROUPS`). See `crm/tests/TEST.md`.
- **Test classification docs** — a capability-gate change (`@requires_cloud` / `@requires_onprem` added or removed on an e2e test) must update the live-run table in `crm/tests/TEST.md`; fixing or reclassifying a defect tracked in `crm/tests/e2e/DISCOVERED_BUGS.md` must update that entry in the same change.

### Running the live e2e suite

Live e2e runs (`D365_E2E=1 pytest -m e2e`) and any live-org verification follow the **`live-e2e`** skill (`.claude/skills/live-e2e/SKILL.md`): live targets (`agent-cloud` preferred, `agent-on-prem` VPN-gated, ephemeral `agent-cs-trial`), creds wiring (`D365_E2E_PROFILE` vs flat `D365_*`), the worktree-code recipe and its tripwires, the cloud host guard, the verify-on-the-reported-target rule, and the fixture-placeholder rule. Pin `--profile <name>` on any live command and confirm the org via `crm connection whoami` before reporting target-specific facts.

`.github/workflows/docs.yml` runs `mkdocs build --strict` on any `crm/**`, `setup.py`, `docs/**`, or `mkdocs.yml` change — **stale refs / broken links fail CI.**

## Release

Releases are cut **automatically** by `python-semantic-release` (`.github/workflows/semantic-release.yml`, config in `pyproject.toml` `[tool.semantic_release]`). Every push to `main` reads the Conventional Commit history since the last tag, bumps the version in BOTH `setup.py` and `crm/__init__.py`, updates `CHANGELOG.md` (`mode=update`, inserted at the `<!-- version list -->` marker), commits `chore(release): vX.Y.Z`, and pushes tag `vX.Y.Z`. So **commit messages drive the bump**: `feat:`→minor, `fix:`/`perf:`→patch, breaking (`!`/`BREAKING CHANGE:`)→major (post-1.0: `allow_zero_version=false`, `major_on_zero=true`).

**Bump discipline — reserve `feat:` for substantial new capability; minor is not the default.** A small enhancement (a new flag/alias on an existing command, a tweak, a polish) ships as `fix:` or `perf:` → **patch** bump. Use `feat:` only for a genuinely new command, a new query mode, or a materially new capability → minor bump. The minor digit tracks real features; the patch digit absorbs the steady stream of small improvements.

The tag push uses **`RELEASE_PAT`**, NOT `GITHUB_TOKEN` — a tag pushed with `GITHUB_TOKEN` does not trigger downstream workflows, so `release.yml` would never fire. PSR itself does not build or create the GitHub release (`vcs_release: false`); the tag fires `release.yml`, which builds the PyInstaller binaries, uploads to R2, and creates the GitHub release. `scripts/check_tag_version.py` still gates that the tag matches `setup.py`.

Manual release (fallback / re-cut): bump both version files, then push the tag yourself (a human/PAT tag push fires `release.yml`).

Any PyInstaller bundle-shape change must touch all **5 sites**:

1. `crm.spec`
2. `.github/workflows/release.yml`
3. `.github/workflows/ci.yml` (the `package` job)
4. `scripts/build.sh`
5. `scripts/build.ps1`

## Agent skills

### Subagents

For code exploration/search, use the **`Explore`** agent and run it on the **haiku** model.

### Issue tracker

Issues live in GitHub Issues at `Gharib89/crm`. Use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. The `/triage` skill drives the front half — categorise an issue/external-PR through this label state machine (verify → grill → write an agent brief) up to `ready-for-agent`; `/ship` then drives the claim lifecycle from there. `/ship` claims by **assignee** (it assigns itself and comments), opens the PR, and merge closes. See `docs/agents/triage-labels.md`.

### Code review

`/ship`'s review loop runs the reviewers in `docs/agents/ship.md` `## Reviewers`: **Copilot** plus a **Claude Code fallback**. CodeRabbit is manual and merge-gate only.

- **Copilot** reviews once, automatically, when a ready PR opens (ruleset *"Copilot auto review"*, `review_on_push: false`). Never re-request it in the ship flow; disposition its threads once (address, or decline with evidence). House rules live in `.github/copilot-instructions.md` (only the first 4000 chars are read). Review effort is set in the repo's Copilot settings, which an agent cannot read; recent reviews report **Lite**.
- **Claude Code** (`.github/workflows/claude-review.yml`) runs on an `@claude` PR comment. Ship posts one when Copilot exits degraded for any reason (never-queued, blocked, silent, infra-error, cap-hit, unreachable). Any OWNER, MEMBER or COLLABORATOR `@claude` comment on a PR one of them authored also fires it. Cap 2 rounds.
- **CodeRabbit** does not auto-review this repo (star-gated) and is not in the ship profile: it is comment-triggered with no workflow, which ship cannot express yet (Gharib89/skills#302). The **`merge-gate`** skill drives it: `@coderabbitai review` while the PR is open (a closed PR declines), after each fix push; `@coderabbitai resolve` only once every thread carries a disposition. Config in `.coderabbit.yaml`.

### PR merge gate

Inbound agent-shipped PRs (cloud-ship routine, codex, teammates' agents) get a second, local review pass via the **`merge-gate`** skill before the maintainer merges: drift checklist + targeted live e2e + scoped fix-in-place + review-bot iteration (the gate posts `@coderabbitai review` after each fix push it makes, since CodeRabbit no longer re-reviews on its own; **one** Copilot re-request permitted only when the gate significantly rewrote the PR — the merge-gate exception to the round-1-only lane), ending in a `gate-passed`/`gate-failed` label. See `docs/agents/pr-merge-gate.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.

### Ship

`/ship` drives one issue to a merge-ready PR. This repo's ship profile: `docs/agents/ship.md`. Without that file ship refuses: run `/setup-skills`.

Every skill `skills-lock.json` lists is a derived copy, changed at its source and refreshed here; `skills-lock.json` records each one's source. `ship`, `cloud-ship`, `setup-skills` and `update-skills` come from `Gharib89/skills`; the skills ship composes come from `mattpocock/skills`, `upstash/context7` and `humanlayer/skills`. Refresh a skill by re-running its install line at project scope, without `-g`. Ship's refresh chains its preflight, so a profile the refreshed ship no longer reads is reported now, not on the next `/ship`: `npx skills add Gharib89/skills --skill ship --skill cloud-ship --skill setup-skills --skill update-skills --agent claude-code -y && .claude/skills/ship/scripts/preflight.sh none`.
