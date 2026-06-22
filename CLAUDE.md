# crm — Project Memory

Python CLI for Microsoft Dynamics 365 Customer Engagement — on-prem v9.x (NTLM) **or** Dataverse online (OAuth client-credentials). Same commands hit both targets, over the Dataverse Web API (OData v4) / HTTPS. Single-package layout (`crm/`), pyright strict on `crm/core/*` and `crm/utils/d365_backend.py`, basic mode elsewhere.

## Credential model

Setup is `crm profile add` (interactive wizard on a TTY; flag-driven for scripting) — infers auth from the URL, saves the secret (OS keyring → `0600` plaintext fallback on WSL/headless), tests via WhoAmI, and activates. Profile verbs: `crm profile add | use | list | edit | rm | set-password | delete-password`. Diagnostics only under `crm connection whoami | test | doctor | status`. **No `.env`, no credential env vars** (`D365_*`/`CRM_*`/`D365_AUTH`/`CRM_AUTH_SCHEME` are gone) — credentials come only from a saved profile or `--password` (per-run override); secret resolution is `--password` > stored plaintext > keyring > TTY prompt. The only retained env knob is `CRM_HOME` (state dir). A connection command with no profile auto-launches `crm profile add` on a TTY; under `--json`/no-TTY it errors with "run `crm profile add`".

## Architecture

- `crm/core/*` — Web API logic, one module per domain (`entity`, `query`, `metadata`, `solution`, …); pyright **strict**.
- `crm/commands/*` — thin Click wrappers, one per `crm <group>`; `crm/cli.py` wires them; `crm/__main__.py` is the entry.
- `crm/skills/` — agent skill shipped in the wheel: a thin `SKILL.md` router + `reference/*.md` loaded on demand (kept in sync with the CLI — see below).

## Branch & worktree discipline

The main checkout (`~/wip/projects/crm`) is shared by concurrent agent sessions — **never develop in it directly**. Any feature or bug fix happens in a **git worktree on a fresh branch**: `EnterWorktree` (or `git worktree add`), rename the auto branch to a clean name (`git branch -m feat/<topic>`), do all work + commits there, PR from that branch, remove the worktree after merge. Worktrees have no `.venv` — verify with `PYTHONPATH=$WT <main-venv>/bin/python -m pytest` (the main venv's editable install points at the main checkout otherwise). In the shared checkout itself: read-only work and small docs-only commits to `main` (via a throwaway worktree if the dir is on someone else's branch). Before **any** git mutation anywhere: `git branch --show-current && git status -sb` first, and stage with explicit paths, never `git add -A`.

## Commands

```bash
pip install -e ".[dev,docs]"              # dev + docs deps
pytest                                    # offline suite; addopts pins `-m 'not e2e'`, so e2e is skipped by default
pytest crm/tests/test_query.py::test_x    # single test (or `-k '<expr>'` to match by name); `-m slow` for the slow ops
pyright --pythonpath .venv/bin/python     # local lint (omit → ~56 false errors); strict mode + py3.9 pinned in pyrightconfig.json
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
- **SKILL ↔ CLI** — `crm/skills/` is the single tracked agent skill (source of truth): a thin `SKILL.md` router + `reference/*.md`. `crm skill install` copies the whole tree into a harness dir outside the repo (`~/.claude/skills/crm/`, etc.). The skill is **self-contained** — it ships to users who have only the skill, not the repo, so never link a shipped skill file to a repo path (`docs/**`, `CONTEXT.md`); inline what's needed. The skill states only what `crm describe`/`--help` cannot (workflows, gotchas, the JSON contract) — **never restate flags/choices/defaults**. Never track an in-repo copy of the **crm** skill (source of truth `crm/skills/`); the one tracked `.claude/skills/` tree holds the cloud routine's `cloud-ship/ship/tdd/review` workflow skills — unlike the crm skill, these have no *separate* source tree, so `.claude/skills/` is itself their source of truth (see `docs/agents/cloud-ship-routine.md`). When editing `crm/skills/`, the docs-sync agent invokes the **`writing-great-skills`** skill (vendored at `.claude/skills/writing-great-skills/` — a copy of the global skill that can drift; re-sync from `~/.claude/skills/writing-great-skills/`) as the authority for skill structure and description rules. See `docs/contributing/skill-and-cli.md`.
- **E2E coverage gate** — every new/changed D365-touching command must ship a live e2e test under `crm/tests/e2e/` stamped `@covers("<group> <verb>")`, **or** an `E2E_SKIP` entry with a reason in `crm/tests/e2e/coverage.py`. The offline gate (`crm/tests/test_e2e_coverage_gate.py`) fails CI otherwise. Local/meta groups (`profile`, `session`, `skill`, `self-update`, `repl`, `scaffold`) are out of scope (`LOCAL_GROUPS`). See `crm/tests/TEST.md`.
- **Test classification docs** — a capability-gate change (`@requires_cloud` / `@requires_onprem` added or removed on an e2e test) must update the live-run table in `crm/tests/TEST.md`; fixing or reclassifying a defect tracked in `crm/tests/e2e/DISCOVERED_BUGS.md` must update that entry in the same change.

### Running the live e2e suite (target + creds)

**Project live targets — use these two profiles for all live/e2e work:** **`agent-on-prem`** (NTLM on-prem v9.1 test org) and **`agent-cloud`** (OAuth / Dataverse online). Pin `--profile <name>` on any live command and confirm the org via `crm connection whoami` before reporting target-specific facts. Prefer **`agent-cloud`** for general verification (always reachable, no VPN); use **`agent-on-prem`** for `requires_onprem` and target-divergent checks. (These supersede the older `crmworx` / `cloud` profiles.)

**Ephemeral CS target — `agent-cs-trial` (ADR 0012).** A third OAuth profile, **`agent-cs-trial`**, may exist pointing at a **Customer-Service-provisioned Dataverse trial** stood up for the CS-dependent e2e verbs that the general `agent-cloud` org can't host (`sla create`/`add-kpi`, `audit detail`, `workflow run`). It is **not** a durable target: a self-service CS trial expires (≤60 days), so **`agent-cloud` stays *the* cloud target and CI stays pointed at it** — never re-point `agent-cloud` or the CI cloud secret at the trial. The CS-verb tests **skip-with-instructions** when the trial is absent (auditing off, no seeded workflow, etc.), so they run only on local, opportunistic `--profile agent-cs-trial` runs while the trial lives, and skip everywhere else. The trial's `*.dynamics.com` host changes each time one is provisioned (kept in local memory, not committed) — set `D365_E2E_ALLOW_HOST` to that host for a local run.

Two ways to give the opt-in suite (`D365_E2E=1`) a live target; pick one:

- **A named profile (preferred for local runs)** — `D365_E2E_PROFILE=<name>` where `<name>` is a profile you already created with `crm profile add`. Its creds + secret are read **read-only** from your real `CRM_HOME` and re-seeded into a throwaway, isolated `CRM_HOME` (your real profiles/session are never mutated). The **target is inferred from the profile's auth scheme**: an OAuth/Dataverse profile → **cloud**, an NTLM profile → **on-prem**. No `D365_*` cred env needed. Prefer a **cloud** profile for general local verification — it's always reachable (no VPN); reserve on-prem for `requires_onprem` and target-divergent tests.
- **Flat `D365_*` env** — set `D365_URL` + creds directly (NTLM: `D365_USERNAME`/`D365_PASSWORD`; OAuth: `D365_AUTH=oauth` + `D365_CLIENT_ID`/`D365_TENANT_ID`/`D365_CLIENT_SECRET`). This is how **CI** runs (secrets → env). Used automatically when `D365_E2E_PROFILE` is unset.

**Both targets** = run the suite once per profile (`D365_E2E_PROFILE=<cloud>` then `=<onprem>`); coverage is the union. There is no single-process "both". **On-prem needs VPN** — if the selected target is unreachable the session **skips** with a "VPN down?" message (any HTTP response, incl 401/403, counts as reachable). Then: `pytest -m e2e`.

**Running WORKTREE code through the e2e `cli` fixture.** The fixture resolves `shutil.which("crm")` → the installed **PyInstaller binary** (`~/.local/bin/crm`), which **ignores `PYTHONPATH`** (it bundles its own code), so an e2e run silently exercises the OLD installed code, not your worktree fix. The venv console-script `.venv/bin/crm` is *also* on PATH and *does* honor `PYTHONPATH`, so stripping only `~/.local/bin` isn't enough. Strip **both** crm dirs so `which` returns nothing and the fixture falls back to `[sys.executable, "-m", "crm"]` (absolute venv python + `PYTHONPATH=$WT` = guaranteed worktree code):

```bash
NEWPATH=$(echo "$PATH"|tr ':' '\n'|grep -vE '/\.local/bin|/crm/\.venv/bin'|paste -sd:)
D365_E2E=1 D365_E2E_PROFILE=<p> PATH=$NEWPATH PYTHONPATH=$WT <main-venv>/bin/python -m pytest -m e2e <node>
```

Tripwire: an e2e that *should* pass with your fix fails **identically to pre-fix** → you're running the frozen binary, not your code.

**Cloud target needs a host-guard override.** The suite refuses destructive runs against a `*.dynamics.com` host (prod-host guard); pass `D365_E2E_ALLOW_HOST=<exact host>` to opt the designated cloud test org in.

**A bug reported on a specific target must be verified on THAT target — cloud-green ≠ fixed.** Cloud (Dataverse online) silently reassigns server-side ids and quietly rewrites/accepts inputs that on-prem v9.x rejects, so a cloud-green run can mask an on-prem-only failure. For an on-prem-reported bug, run the on-prem leg (`D365_E2E_PROFILE=<onprem>`), not just cloud.

**Test fixtures: never embed real-org identifiers** (public repo). Don't copy GUIDs from a live export into test constants — a captured form/record/role GUID can carry the org's machine fingerprint (e.g. a `…00155d467b90` suffix). Use obvious placeholders (`1111…`, `cccc…`).

`.github/workflows/docs.yml` runs `mkdocs build --strict` on any `crm/**`, `setup.py`, `docs/**`, or `mkdocs.yml` change — **stale refs / broken links fail CI.**

## Release

Releases are cut **automatically** by `python-semantic-release` (`.github/workflows/semantic-release.yml`, config in `pyproject.toml` `[tool.semantic_release]`). Every push to `main` reads the Conventional Commit history since the last tag, bumps the version in BOTH `setup.py` and `crm/__init__.py`, updates `CHANGELOG.md` (`mode=update`, inserted at the `<!-- version list -->` marker), commits `chore(release): vX.Y.Z`, and pushes tag `vX.Y.Z`. So **commit messages drive the bump**: `feat:`→minor, `fix:`/`perf:`→patch, breaking (`!`/`BREAKING CHANGE:`)→major (post-1.0: `allow_zero_version=false`, `major_on_zero=true`).

**Bump discipline — reserve `feat:` for substantial new capability; minor is not the default.** A small enhancement (a new flag/alias on an existing command, a tweak, a polish) ships as `fix:` or `perf:` → **patch** bump. Use `feat:` only for a genuinely new command, a new query mode, or a materially new capability → minor bump. This keeps the version from inflating one minor per issue (the v4.22 problem): the minor digit tracks real features, the patch digit absorbs the steady stream of small improvements — same semantics as Claude Code's own versioning.

The tag push uses **`RELEASE_PAT`**, NOT `GITHUB_TOKEN` — a tag pushed with `GITHUB_TOKEN` does not trigger downstream workflows, so `release.yml` would never fire. PSR itself does not build or create the GitHub release (`vcs_release: false`); the tag fires `release.yml`, which builds the PyInstaller binaries, uploads to R2, and creates the GitHub release. `scripts/check_tag_version.py` still gates that the tag matches `setup.py`.

Manual release (fallback / re-cut): bump both version files, then push the tag yourself (a human/PAT tag push fires `release.yml`). Any PyInstaller bundle-shape change must touch all 5 sites: `crm.spec`, `.github/workflows/release.yml`, `.github/workflows/ci.yml` (the `package` job), `scripts/build.sh`, `scripts/build.ps1`.

## Agent skills

### Subagents

For code exploration/search, use the **`Explore`** agent (not the `cavecrew-*` agents) and run it on the **haiku** model.

### Issue tracker

Issues live in GitHub Issues at `Gharib89/crm`. Use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`, plus `agent-working` (claimed by `/ship`). `/ship` drives the claim lifecycle itself — phase 1 claims (`ready-for-agent` → `agent-working` + comment), phase 6 comments the PR link, merge closes. See `docs/agents/triage-labels.md`.

### Code review

Copilot code review **auto-triggers on PR creation** for ready (non-draft) PRs, via the repo ruleset *"Copilot auto review"* (target: default branch) — **round 1 fires automatically; never re-request it.** Re-review does **not** trigger on push (`review_on_push: false`, deliberate: there's no per-PR review cap, so manual rounds preserve a ~3-round ceiling). Request later rounds via REST (the `gh pr edit --add-reviewer copilot` path fails on this repo):

```bash
echo '{"reviewers":["copilot-pull-request-reviewer[bot]"]}' | \
  gh api -X POST repos/Gharib89/crm/pulls/<n>/requested_reviewers --input -
```

Then verify `requested_reviewers` actually populated — a bare HTTP 201 can silently no-op (passing the display name `"Copilot"` instead of the bot login is the classic trap). Review effort is **Medium** (the only model lever); house rules + a known-non-issues list live in `.github/copilot-instructions.md` (only its first 4000 chars are read).

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
