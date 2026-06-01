# CRMWorx Guide — Plan 1: MkDocs Scaffold (offline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a MkDocs Material documentation site with an auto-generated CLI reference and empty slots for the CRMWorx walkthrough, buildable with zero server access.

**Architecture:** Reuse the existing `docs/` tree as the MkDocs `docs_dir`. Explicitly exclude the pre-existing internal docs (`adr/`, `agents/`, `research/`, `superpowers/`) and the legacy hand-rolled `index.html` from the build so `mkdocs build --strict` stays clean. The CLI reference is generated from the Click app via `mkdocs-click`. A `docs.yml` GitHub Actions workflow deploys to GitHub Pages on push to `main`.

**Tech Stack:** MkDocs, mkdocs-material, mkdocs-click, GitHub Actions, Python ≥3.9.

---

## File structure

- Create: `mkdocs.yml` (site config, nav, theme, plugins)
- Create: `docs/index.md` (home)
- Create: `docs/getting-started/install.md`, `docs/getting-started/configure.md` (thin, link to README)
- Create: `docs/guides/crmworx-walkthrough.md` (stub, filled by Plan 2)
- Create: `docs/how-to/{connection,entity,query,metadata,solution,data,action}.md` (stubs, filled by Plan 3)
- Create: `docs/reference/cli.md` (mkdocs-click directive)
- Create: `docs/contributing/skill-and-cli.md` (stub, filled by Plan 3)
- Modify: `setup.py` (add `docs` extras group)
- Create: `.github/workflows/docs.yml` (Pages deploy)
- Preserve, exclude from build: `docs/index.html`, `docs/adr/`, `docs/agents/`, `docs/research/`, `docs/superpowers/`

### Task 1: Add the `docs` extras dependency group

**Files:**
- Modify: `setup.py:30-33` (the `extras_require` dict)

- [ ] **Step 1: Add the docs extras**

In `setup.py`, change `extras_require` to add a `docs` key:

```python
    extras_require={
        "dev": ["pytest>=7.0", "requests_mock>=1.10", "pyinstaller>=6.0", "pyright>=1.1.380"],
        "kerberos": ["requests_negotiate_sspi"],
        "docs": ["mkdocs>=1.6", "mkdocs-material>=9.5", "mkdocs-click>=0.8"],
    },
```

- [ ] **Step 2: Install the docs extras**

Run: `pip install -e .[docs]`
Expected: installs mkdocs, mkdocs-material, mkdocs-click without error. Verify: `mkdocs --version` prints a version ≥ 1.6.

- [ ] **Step 3: Commit**

```bash
git add setup.py
git commit -m "build: add docs extras group (mkdocs + material + click)"
```

### Task 2: Create the home and getting-started pages

**Files:**
- Create: `docs/index.md`
- Create: `docs/getting-started/install.md`
- Create: `docs/getting-started/configure.md`

- [ ] **Step 1: Write `docs/index.md`**

```markdown
# crm — Dynamics 365 CE on-prem CLI

A stateful CLI for **Microsoft Dynamics 365 Customer Engagement (on-premises) 9.x**,
wrapping the Dataverse Web API (OData v4) over NTLM. Built for shell scripting and
AI agents: `--json` everywhere, `--dry-run` to preview mutations.

## Where to go next

- **[Install](getting-started/install.md)** — binary or from source.
- **[Configure](getting-started/configure.md)** — credentials and profiles.
- **[CRMWorx walkthrough](guides/crmworx-walkthrough.md)** — build a full ticketing +
  SLA customization end to end with Claude Code.
- **[How-to guides](how-to/connection.md)** — task recipes per command group.
- **[CLI reference](reference/cli.md)** — every command and flag, generated from source.
```

- [ ] **Step 2: Write `docs/getting-started/install.md`**

```markdown
# Install

The canonical install instructions live in the project
[README](https://github.com/Gharib89/crm#install) and are summarised here.

## Prebuilt binary

Download the latest release for your platform from the
[releases page](https://github.com/Gharib89/crm/releases/latest) and put it on your `PATH`.

## From source

```bash
pip install -e .
crm --version
```

Requires Python ≥ 3.9. See the README for the full per-platform walkthrough.
```

- [ ] **Step 3: Write `docs/getting-started/configure.md`**

```markdown
# Configure

The CLI authenticates with **NTLM (Windows Integrated)**. Set the `D365_*` env vars
(or `CRM_*` aliases):

```bash
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."        # never persisted to disk
export D365_DOMAIN="CONTOSO"      # optional if username is a UPN
```

Or save a reusable profile, including the default solution and publisher prefix used
by metadata write commands:

```bash
crm connection connect \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --default-solution CRMWorx --publisher-prefix cwx \
    --profile-name crmworx
```

State lives under `~/.crm/` (override with `CRM_HOME`). See the
[README](https://github.com/Gharib89/crm#configure) for the full reference.
```

- [ ] **Step 4: Commit**

```bash
git add docs/index.md docs/getting-started/
git commit -m "docs: add home + getting-started pages"
```

### Task 3: Create stub pages for the walkthrough, how-tos, and contributing

**Files:**
- Create: `docs/guides/crmworx-walkthrough.md`
- Create: `docs/how-to/connection.md`, `entity.md`, `query.md`, `metadata.md`, `solution.md`, `data.md`, `action.md`
- Create: `docs/contributing/skill-and-cli.md`

- [ ] **Step 1: Write the walkthrough stub `docs/guides/crmworx-walkthrough.md`**

```markdown
# CRMWorx walkthrough

> **Status:** scaffold. Filled in by the live run (Plan 2).

This guide builds **CRMWorx** — an IT-company ticketing platform with SLA — end to
end using the `crm` CLI, demonstrating every command group. The steps below are
placeholders; each will be replaced with the real command and its captured output.

1. Pre-flight & connection
2. Metadata build (option sets → entities → attributes → relationships)
3. Seed data
4. Read & verify
5. Package the solution
6. Teardown (optional, for a clean replay)
```

- [ ] **Step 2: Write each how-to stub**

For each group `G` in {connection, entity, query, metadata, solution, data, action}, create `docs/how-to/G.md`:

```markdown
# How-to: <G>

> **Status:** scaffold. Filled in by Plan 3 from the live run.

Task recipes for the `crm <G>` command group. See the
[CLI reference](../reference/cli.md) for the exhaustive flag list.
```

(Replace `<G>` with the group name in each file's title.)

- [ ] **Step 3: Write `docs/contributing/skill-and-cli.md`**

```markdown
# Keeping the agent skill in sync with the CLI

> **Status:** scaffold. Filled in by Plan 3.

The agent skill source of truth is `crm/skills/SKILL.md`. Installing it into an agent
copies that file:

```bash
crm skill install --target claude --force
```

This page documents the sync workflow and the bug loop used while building CRMWorx.
```

- [ ] **Step 4: Commit**

```bash
git add docs/guides docs/how-to docs/contributing
git commit -m "docs: add walkthrough, how-to, and contributing stubs"
```

### Task 4: Generate the CLI reference with mkdocs-click

**Files:**
- Create: `docs/reference/cli.md`

- [ ] **Step 1: Write `docs/reference/cli.md`**

The `mkdocs-click` directive renders the whole Click tree. The console entry point is
`crm = crm.cli:cli` (see `setup.py`), so the module is `crm.cli` and the command is `cli`:

```markdown
# CLI reference

This page is generated from the `crm` command tree.

::: mkdocs-click
    :module: crm.cli
    :command: cli
    :prog_name: crm
    :depth: 1
```

- [ ] **Step 2: Commit**

```bash
git add docs/reference/cli.md
git commit -m "docs: add auto-generated CLI reference page"
```

### Task 5: Write `mkdocs.yml` with explicit nav and exclusions

**Files:**
- Create: `mkdocs.yml`

- [ ] **Step 1: Write `mkdocs.yml`**

`exclude_docs` (mkdocs ≥1.5) keeps the pre-existing internal docs and the legacy
`index.html` out of the build so `--strict` stays clean. `nav` is explicit.

```yaml
site_name: crm — D365 CE on-prem CLI
site_description: CLI + agent guide for Dynamics 365 CE on-premises customizations
repo_url: https://github.com/Gharib89/crm
repo_name: Gharib89/crm
docs_dir: docs

theme:
  name: material
  features:
    - navigation.sections
    - navigation.top
    - content.code.copy
    - content.code.annotate
  palette:
    - scheme: default
      primary: deep orange
      accent: amber

plugins:
  - search

markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.highlight
  - tables
  - mkdocs-click

exclude_docs: |
  index.html
  adr/
  agents/
  research/
  superpowers/

nav:
  - Home: index.md
  - Getting started:
      - Install: getting-started/install.md
      - Configure: getting-started/configure.md
  - Guides:
      - CRMWorx walkthrough: guides/crmworx-walkthrough.md
  - How-to:
      - connection: how-to/connection.md
      - entity: how-to/entity.md
      - query: how-to/query.md
      - metadata: how-to/metadata.md
      - solution: how-to/solution.md
      - data: how-to/data.md
      - action: how-to/action.md
  - Reference:
      - CLI: reference/cli.md
  - Contributing:
      - Skill & CLI sync: contributing/skill-and-cli.md
```

- [ ] **Step 2: Build the site with strict mode**

Run: `mkdocs build --strict`
Expected: `INFO - Documentation built in ...` with **no WARNING lines**. Specifically, no "is not included in the nav" warnings for `adr/`, `agents/`, `research/`, or `superpowers/`, and no conflict on `index.html`/`index.md`.

If warnings appear for any internal file, add its path to `exclude_docs` and rebuild.

- [ ] **Step 3: Verify the CLI reference rendered**

Run: `grep -rl "connection" site/reference/` 
Expected: the built `site/reference/cli/index.html` contains the command tree (groups like `connection`, `entity`, `metadata`). If empty, confirm `mkdocs-click` is installed and the `:module:`/`:command:` values match `setup.py`'s entry point.

- [ ] **Step 4: Ignore the build output**

Add `site/` to `.gitignore` if not already present:

Run: `grep -qxF 'site/' .gitignore || echo 'site/' >> .gitignore`

- [ ] **Step 5: Commit**

```bash
git add mkdocs.yml .gitignore
git commit -m "docs: add mkdocs config with strict-clean nav and exclusions"
```

### Task 6: Add the GitHub Pages deploy workflow

**Files:**
- Create: `.github/workflows/docs.yml`

- [ ] **Step 1: Write `.github/workflows/docs.yml`**

```yaml
name: docs
on:
  push:
    branches: [main]
    paths:
      - 'docs/**'
      - 'mkdocs.yml'
      - 'crm/**'
      - '.github/workflows/docs.yml'
permissions:
  contents: write
jobs:
  deploy:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install docs deps
        run: pip install -e .[docs]
      - name: Build (strict)
        run: mkdocs build --strict
      - name: Deploy to gh-pages
        run: mkdocs gh-deploy --force
```

- [ ] **Step 2: Validate the workflow YAML locally**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/docs.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docs.yml
git commit -m "ci: add mkdocs GitHub Pages deploy workflow"
```

## Self-review notes

- The legacy `docs/index.html` is **excluded, not deleted** — it remains in the repo;
  GitHub Pages is driven by `mkdocs gh-deploy` (gh-pages branch) once the workflow runs.
- Plans 2 and 3 fill the stubs created here; no stub content is final.
- No server access is required for any task in this plan.
