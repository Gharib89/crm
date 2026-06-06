# crm — Project Memory

Python CLI for Microsoft Dynamics 365 Customer Engagement — on-prem v9.x (NTLM) **or** Dataverse online (OAuth client-credentials). Same commands hit both targets, over the Dataverse Web API (OData v4) / HTTPS. Single-package layout (`crm/`), pyright strict on `crm/core/*` and `crm/utils/d365_backend.py`, basic mode elsewhere.

## Architecture

- `crm/core/*` — Web API logic, one module per domain (`entity`, `query`, `metadata`, `solution`, …); pyright **strict**.
- `crm/commands/*` — thin Click wrappers, one per `crm <group>`; `crm/cli.py` wires them; `crm/__main__.py` is the entry.
- `crm/skills/SKILL.md` — agent-skill copy shipped in the wheel (kept in sync with the CLI — see below).

## Commands

```bash
pip install -e ".[dev,docs]"              # dev + docs deps
pytest                                    # tests (E2E need live D365 creds in .env)
pyright --pythonpath .venv/bin/python     # local lint (omit → ~56 false errors)
mkdocs build --strict                     # docs; CI runs this, warnings fail
```

## Keep docs in sync with code

Every feature / new command / flag / behavior change ships its docs in the **same** change:

- **README.md** — user-facing capability or install change.
- **CHANGELOG.md** — entry under `## [Unreleased]` (Keep a Changelog format).
- **docs/** — matching `docs/how-to/<group>.md` and `docs/reference/cli.md`.
- **SKILL ↔ CLI** — `crm/skills/SKILL.md` is the single tracked agent skill (source of truth); `crm skill install` copies it into a harness dir outside the repo (`~/.claude/skills/crm/`, etc.). Never track an in-repo `.claude/skills/` copy. See `docs/contributing/skill-and-cli.md`.

`.github/workflows/docs.yml` runs `mkdocs build --strict` on any `crm/**`, `setup.py`, `docs/**`, or `mkdocs.yml` change — **stale refs / broken links fail CI.**

## Release

Releases are cut **automatically** by `python-semantic-release` (`.github/workflows/semantic-release.yml`, config in `pyproject.toml` `[tool.semantic_release]`). Every push to `main` reads the Conventional Commit history since the last tag, bumps the version in BOTH `setup.py` and `crm/__init__.py`, updates `CHANGELOG.md` (`mode=update`, inserted at the `<!-- version list -->` marker), commits `chore(release): vX.Y.Z`, and pushes tag `vX.Y.Z`. So **commit messages drive the bump**: `feat:`→minor, `fix:`/`perf:`→patch, breaking→minor while pre-1.0 (`allow_zero_version=true`, `major_on_zero=false`).

The tag push uses **`RELEASE_PAT`**, NOT `GITHUB_TOKEN` — a tag pushed with `GITHUB_TOKEN` does not trigger downstream workflows, so `release.yml` would never fire. PSR itself does not build or create the GitHub release (`vcs_release: false`); the tag fires `release.yml`, which builds the PyInstaller binaries, uploads to R2, and creates the GitHub release. `scripts/check_tag_version.py` still gates that the tag matches `setup.py`.

Manual release (fallback / re-cut): bump both version files, then push the tag yourself (a human/PAT tag push fires `release.yml`). Any PyInstaller bundle-shape change must touch all 5 sites: `crm.spec`, `.github/workflows/release.yml`, `.github/workflows/build.yml`, `scripts/build.sh`, `scripts/build.ps1`.

## Agent skills

### Issue tracker

Issues live in GitHub Issues at `Gharib89/crm`. Use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
