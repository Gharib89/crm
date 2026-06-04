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

Bump version in BOTH `setup.py` and `crm/__init__.py`. Push tag `vX.Y.Z` → `release.yml` builds (`scripts/check_tag_version.py` gates the tag). Any PyInstaller bundle-shape change must touch all 5 sites: `crm.spec`, `.github/workflows/release.yml`, `.github/workflows/build.yml`, `scripts/build.sh`, `scripts/build.ps1`.

## Agent skills

### Issue tracker

Issues live in GitHub Issues at `Gharib89/crm`. Use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
