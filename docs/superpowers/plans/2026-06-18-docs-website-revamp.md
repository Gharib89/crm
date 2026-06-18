# Docs Website Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the MkDocs site into a beginner-friendly Getting Started journey and an attractive product home page, using only Material for MkDocs primitives and the existing ITWorx brand.

**Architecture:** Docs-only change. Add three markdown extensions to `mkdocs.yml`, restructure the Getting Started `nav` into a 12-page first-run journey, rewrite `docs/index.md` as a hero + feature-card grid, and add CSS to `docs/stylesheets/extra.css`. No CLI/Python code changes. The `how-to/*` pages remain the source of truth for flags; Getting Started pages state the *workflow* and link down.

**Tech Stack:** MkDocs + Material for MkDocs (9.x), `pymdownx`, `mkdocs-click`, `mkdocs-llmstxt`. Verification is `mkdocs build --strict`.

## Global Constraints

- **Docs-in-sync rule (CLAUDE.md):** Getting Started states the *workflow*; never restate flag/choice/default tables — link to the matching `how-to/*` page. (Skill-sync gate.)
- **No content deleted:** `how-to/self-update.md`, `how-to/completion.md`, `how-to/skill.md` stay intact as the flag reference.
- **`mkdocs build --strict` must pass** — warnings (broken links, pages-not-in-nav) fail CI (`.github/workflows/docs.yml`).
- **Facts must match code/`--help`** — all content here is drawn verbatim-in-substance from existing `docs/`, `README.md`, `crm/skills/SKILL.md`. Do not invent behavior.
- **Branch discipline (CLAUDE.md):** work in a git worktree on branch `docs/website-revamp`, never the shared main checkout. Worktree has no `.venv`; run the main venv's mkdocs from the worktree dir.
- **Build command (from worktree dir `$WT`):** `~/wip/projects/crm/.venv/bin/mkdocs build` (non-strict, per-task) / `~/wip/projects/crm/.venv/bin/mkdocs build --strict` (final gate). mkdocs reads `$WT/mkdocs.yml` + `$WT/docs`; it imports the `crm` package from the main venv to render `reference/cli.md` (CLI is unchanged, so this is fine).
- **Brand:** ITWorx indigo `#1B1F8F` / royal `#353A9C` / red accent `#C83428`, already defined as CSS vars in `extra.css`. Reuse them; do not introduce new colors.

---

## File Structure

- `mkdocs.yml` — add 3 markdown extensions; replace Getting Started `nav` block; update `llmstxt` section list.
- `docs/index.md` — full rewrite (hero + cards + first taste + why + agents strip).
- `docs/stylesheets/extra.css` — append home-page hero/button CSS.
- `docs/getting-started/quickstart.md` — NEW.
- `docs/getting-started/concepts.md` — NEW.
- `docs/getting-started/install.md` — rework (tabs + verify line).
- `docs/getting-started/update.md` — NEW.
- `docs/getting-started/add-profile.md` — `git mv` of `initialize.md`, then rework.
- `docs/getting-started/configure.md` — extend (day-2 verbs).
- `docs/getting-started/verify.md` — NEW.
- `docs/getting-started/completion.md` — NEW (beginner framing).
- `docs/getting-started/skill.md` — NEW (beginner framing).
- `docs/getting-started/agent.md` — NEW (text transcript).
- `docs/getting-started/troubleshooting.md` — NEW.

> Note: `docs/getting-started/skill.md` and `docs/getting-started/completion.md` are NEW files distinct from `docs/how-to/skill.md` / `docs/how-to/completion.md` (which are untouched). They live in different directories — no collision.

---

## Task 1: Worktree, extensions, nav, rename

Sets up the workspace and lands all `mkdocs.yml` structural changes up front. After this task the full nav exists; subsequent tasks fill in the referenced page files. Per-task builds are **non-strict** (they tolerate the not-yet-created pages as warnings); the final task runs `--strict`.

**Files:**
- Create: worktree on branch `docs/website-revamp`
- Modify: `mkdocs.yml`
- Rename: `docs/getting-started/initialize.md` → `docs/getting-started/add-profile.md` (`git mv`)

- [ ] **Step 1: Create the worktree** (via `superpowers:using-git-worktrees`, or directly)

```bash
cd ~/wip/projects/crm
git worktree add -b docs/website-revamp ../crm-docs-revamp main
export WT=~/wip/projects/crm-docs-revamp
cd "$WT"
```

- [ ] **Step 2: Rename the initialize page**

```bash
cd "$WT"
git mv docs/getting-started/initialize.md docs/getting-started/add-profile.md
```

- [ ] **Step 3: Add the three markdown extensions to `mkdocs.yml`**

Replace the `markdown_extensions:` block (currently `admonition` / `pymdownx.details` / `pymdownx.superfences` / `pymdownx.highlight` / `tables` / `mkdocs-click`) with:

```yaml
markdown_extensions:
  - admonition
  - attr_list
  - md_in_html
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.highlight
  - pymdownx.tabbed:
      alternate_style: true
  - tables
  - mkdocs-click
```

`attr_list` → `.md-button` classes + card-list grid; `md_in_html` → `<div class="grid cards" markdown>`; `pymdownx.tabbed` → the Windows/Linux content tabs (the theme enables the `content.tabs.link` *feature* but not the rendering extension).

- [ ] **Step 4: Replace the Getting Started `nav` block in `mkdocs.yml`**

Replace:

```yaml
  - Getting started:
      - Install: getting-started/install.md
      - Initialize: getting-started/initialize.md
      - Configure: getting-started/configure.md
```

with:

```yaml
  - Getting started:
      - Quickstart: getting-started/quickstart.md
      - Concepts: getting-started/concepts.md
      - Install: getting-started/install.md
      - Update: getting-started/update.md
      - Add a profile: getting-started/add-profile.md
      - Configure & switch: getting-started/configure.md
      - Verify it works: getting-started/verify.md
      - Tab completion (optional): getting-started/completion.md
      - Install the skill: getting-started/skill.md
      - Use /crm with an agent: getting-started/agent.md
      - Troubleshooting: getting-started/troubleshooting.md
```

- [ ] **Step 5: Update the `llmstxt` plugin section list in `mkdocs.yml`**

Replace the `Getting started:` block under `plugins: > llmstxt: > sections:`:

```yaml
        Getting started:
          - getting-started/install.md: Install (script or from source)
          - getting-started/initialize.md: First-run setup
          - getting-started/configure.md: Auth (NTLM / OAuth) and profiles
```

with:

```yaml
        Getting started:
          - getting-started/quickstart.md: Quickstart — install to first query
          - getting-started/concepts.md: Concepts — on-prem vs cloud, profiles
          - getting-started/install.md: Install (script or from source)
          - getting-started/update.md: Update the CLI
          - getting-started/add-profile.md: Create a connection profile
          - getting-started/configure.md: Auth (NTLM / OAuth) and switching profiles
          - getting-started/skill.md: Install the agent skill
          - getting-started/agent.md: Use /crm with a coding agent
```

(Quickstart-level subset for the agent index; not every page needs listing.)

- [ ] **Step 6: Non-strict build to confirm config parses**

Run: `cd "$WT" && ~/wip/projects/crm/.venv/bin/mkdocs build 2>&1 | tail -20`
Expected: build completes (exit 0). WARNINGs about the not-yet-created pages (`quickstart.md`, `concepts.md`, etc.) and `add-profile.md` not-in-nav-mismatch are expected at this stage. No `Config value 'markdown_extensions'` errors, no traceback.

- [ ] **Step 7: Commit**

```bash
cd "$WT"
git add mkdocs.yml docs/getting-started/add-profile.md
git commit -m "docs: restructure getting-started nav and add md extensions"
```

---

## Task 2: Home page

**Files:**
- Modify: `docs/index.md` (full rewrite)
- Modify: `docs/stylesheets/extra.css` (append)

**Interfaces:**
- Consumes: nav targets from Task 1 (`getting-started/quickstart.md`).
- Produces: hero/button/grid CSS classes `.crm-hero`, `.crm-hero .md-button` (consumed by no later task).

- [ ] **Step 1: Rewrite `docs/index.md`**

````markdown
---
hide:
  - navigation
  - toc
---

<div class="crm-hero" markdown>

![crm](assets/logo.svg){ .crm-hero-logo }

# crm

### Drive Dynamics 365 CE from your shell — on-prem (NTLM) or Dataverse (OAuth), one CLI.

[Get started](getting-started/quickstart.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/Gharib89/crm){ .md-button }

</div>

<div class="grid cards" markdown>

-   __Records__

    ---

    Create, read, update, delete and bulk-import accounts, contacts, and custom
    entities over the Dataverse Web API.

    [entity how-to →](how-to/entity.md)

-   __Queries__

    ---

    OData v4 (`$filter`/`$select`/`$top`) or FetchXML, with `--json` output ready
    for scripts and agents.

    [query how-to →](how-to/query.md)

-   __Solutions__

    ---

    Export, import, clone-as-patch, stage-and-upgrade, and uninstall managed and
    unmanaged solutions.

    [solution how-to →](how-to/solution.md)

-   __Metadata__

    ---

    Browse and write entity, attribute, and relationship definitions; declarative
    `apply` from a spec file.

    [metadata how-to →](how-to/metadata.md)

-   __Plug-ins__

    ---

    Register plug-in assemblies and steps, manage workflows, SLAs, and async
    operations.

    [plugin how-to →](how-to/plugin.md)

-   __/crm agent skill__

    ---

    Ships an agent skill so Claude Code or Copilot CLI can drive D365 for you in
    plain language.

    [use with an agent →](getting-started/agent.md)

</div>

## A first taste

```bash
crm profile add                  # (1)!
crm query whoami --json          # (2)!
```

1. One-time interactive wizard: enter your server URL, the CLI infers NTLM vs
   OAuth, stores the secret, and verifies the connection.
2. Confirms you're connected — prints your user id and org, machine-readable.

## Why crm

- **`--json` everywhere** — every command emits a structured envelope for agents and scripts.
- **`--dry-run`** — preview mutations before they touch the server.
- **One CLI, both targets** — the same commands hit on-prem v9.x and Dataverse online.
- **Optional metadata cache** — `CRM_CACHE_METADATA=1` speeds up repeated one-shot agent calls.

## For agents

Point a web-fetch agent at [`/llms.txt`](llms.txt) (curated index) or
[`/llms-full.txt`](llms-full.txt) (full corpus), or read the complete
[CLI reference](reference/cli.md).
````

- [ ] **Step 2: Append home-page CSS to `docs/stylesheets/extra.css`**

```css
/* ---- Home page hero ---------------------------------------------------- */
.crm-hero {
  text-align: center;
  padding: 2.5rem 1rem 1.5rem;
}
.crm-hero-logo {
  width: 72px;
  height: 72px;
}
.crm-hero h1 {
  margin: 0.4rem 0 0;
  font-weight: 700;
}
.crm-hero h3 {
  margin: 0.4rem auto 1.4rem;
  max-width: 36rem;
  font-weight: 400;
  color: var(--md-default-fg-color--light);
}
.crm-hero .md-button {
  margin: 0.3rem 0.4rem;
}
```

- [ ] **Step 3: Build and confirm the home page renders**

Run: `cd "$WT" && ~/wip/projects/crm/.venv/bin/mkdocs build 2>&1 | tail -15 && test -f site/index.html && grep -q "A first taste" site/index.html && echo HOME_OK`
Expected: `HOME_OK` printed; no error about `assets/logo.svg` (it already exists — it's the theme logo).

- [ ] **Step 4: Commit**

```bash
cd "$WT"
git add docs/index.md docs/stylesheets/extra.css
git commit -m "docs: rebuild home page with hero and feature-card grid"
```

---

## Task 3: Quickstart + Concepts pages

**Files:**
- Create: `docs/getting-started/quickstart.md`
- Create: `docs/getting-started/concepts.md`

- [ ] **Step 1: Write `docs/getting-started/quickstart.md`**

````markdown
# Quickstart

From nothing to a working query in about five minutes.

## 1. Install

=== "Windows (PowerShell)"

    ```powershell
    irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
    ```

=== "Linux"

    ```bash
    curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
    ```

The prebuilt binary bundles Python — nothing else to install. See
[Install](install.md) for `uv` and from-source options, and for managed machines
where the binary is blocked.

## 2. Open a new shell

So your `PATH` picks up `crm`, then confirm:

```bash
crm --version
```

## 3. Create a profile

```bash
crm profile add
```

On a terminal this runs a wizard: enter your server URL, and the CLI infers the
auth scheme (`*.dynamics.com` → OAuth, anything else → NTLM), prompts for what
that scheme needs, stores the secret, runs a `WhoAmI` to verify, and activates the
profile. See [Add a profile](add-profile.md) for the non-interactive form.

## 4. Confirm it works

```bash
crm connection whoami
crm query account --top 5
```

If `whoami` prints your user and organization, you're connected.

---

**Next:**

- [Install the skill](skill.md) and [use `/crm` with a coding agent](agent.md).
- Browse the [how-to guides](../how-to/connection.md) for task recipes.
- Hit a snag? See [Troubleshooting](troubleshooting.md).
````

- [ ] **Step 2: Write `docs/getting-started/concepts.md`**

````markdown
# Concepts

A few terms used throughout these docs.

## On-prem vs cloud

`crm` talks to two kinds of Dynamics 365 CE servers. It picks the auth scheme from
your server URL — you don't choose it manually.

| | On-premises | Cloud (Dataverse online) |
|---|---|---|
| URL shape | `https://crm.contoso.local/org` | `https://contoso.crm.dynamics.com` |
| Auth | **NTLM** (Windows Integrated) | **OAuth 2.0** client-credentials |
| You provide | username (+ domain) and password | tenant id, client id, client secret |
| API version | caps at v9.1 (auto-negotiated) | v9.2 |

The same `crm` commands work against both targets.

## Profile

A **profile** is a saved connection: the server URL, the auth scheme, the identity
fields, and the secret. You create one with [`crm profile add`](add-profile.md) and
switch between several with `crm profile use`. There is no `.env` file and no
credential environment variables — credentials live only in a profile.

State (profiles, cached tokens, completion scripts) lives under `~/.crm/`. The only
environment knob that affects connections is `CRM_HOME`, which relocates that
directory.

## Solution and publisher prefix

In Dynamics, customizations belong to a **solution**, and new schema names carry a
**publisher prefix** (e.g. `cwx_caseid`). Metadata-write commands need both. Attach
defaults to a profile so you don't pass them every time:

```bash
crm profile add --url ... --default-solution CRMWorx --publisher-prefix cwx --name crmworx
```

See [Configure & switch](configure.md) for the full field reference.
````

- [ ] **Step 3: Build and confirm both pages render**

Run: `cd "$WT" && ~/wip/projects/crm/.venv/bin/mkdocs build 2>&1 | tail -10 && test -f site/getting-started/quickstart/index.html && test -f site/getting-started/concepts/index.html && echo PAGES_OK`
Expected: `PAGES_OK`. (Material builds each page to `<name>/index.html`.)

- [ ] **Step 4: Commit**

```bash
cd "$WT"
git add docs/getting-started/quickstart.md docs/getting-started/concepts.md
git commit -m "docs: add quickstart and concepts getting-started pages"
```

---

## Task 4: Install rework + Update page

**Files:**
- Modify: `docs/getting-started/install.md`
- Create: `docs/getting-started/update.md`

- [ ] **Step 1: Rework `docs/getting-started/install.md`**

Keep all existing content (install script, `uv tool install`, from source, integrity
verification). Make two changes:

(a) Replace the `## Install script (no Python required)` heading's two code blocks
(the `**Windows (PowerShell):**` / `**Linux:**` bold-label form) with content tabs:

```markdown
## Install script (no Python required)

=== "Windows (PowerShell)"

    ```powershell
    irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
    ```

=== "Linux"

    ```bash
    curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
    ```
```

(Leave the `Pin a version…` paragraph and everything below the tabs unchanged.)

(b) At the very end of the file, after the `## From source` section, append:

```markdown
## Verify

```bash
crm --version
```

Then create a connection with [Add a profile](add-profile.md), or jump to the
[Quickstart](quickstart.md).
```

- [ ] **Step 2: Write `docs/getting-started/update.md`**

````markdown
# Update

Keep the `crm` CLI current.

## Check for a newer release

```bash
crm self-update --check
```

Reports your running version, the latest published version, and whether an update is
available — without changing anything. Works on every install type.

## Upgrade in place

```bash
crm self-update
```

For a binary installed via the install script, this downloads the platform archive,
verifies it against the published `SHA256SUMS`, and swaps the bundle in place. A
checksum mismatch or download failure leaves your install untouched.

For `pip` / `uv` / source installs, `self-update` doesn't touch the binary — it
points you at `pip install -U crm` (or re-running `uv tool install`).

A non-`--check` update also re-syncs any agent skills you installed (see
[Install the skill](skill.md)), so the shipped skill never lags the CLI.

## The passive update notice

On an interactive terminal, `crm` checks at most once every 24 hours for a newer
release and prints a one-line notice on stderr after a command finishes. It is
silent under `--json`, when stderr isn't a terminal, when `CI` is set, and when
`CRM_NO_UPDATE_CHECK` is set. Opt out permanently:

```bash
export CRM_NO_UPDATE_CHECK=1
```

See [how-to: self-update](../how-to/self-update.md) for the per-destination
skill-sync detail and the full flag reference.
````

- [ ] **Step 3: Build and confirm**

Run: `cd "$WT" && ~/wip/projects/crm/.venv/bin/mkdocs build 2>&1 | tail -10 && test -f site/getting-started/update/index.html && grep -q "Verify" site/getting-started/install/index.html && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
cd "$WT"
git add docs/getting-started/install.md docs/getting-started/update.md
git commit -m "docs: add install tabs and a getting-started update page"
```

---

## Task 5: Add a profile + Configure & switch

**Files:**
- Modify: `docs/getting-started/add-profile.md` (the renamed `initialize.md`)
- Modify: `docs/getting-started/configure.md`

- [ ] **Step 1: Rewrite `docs/getting-started/add-profile.md`**

````markdown
# Add a profile

A working setup is a single saved **profile** — the server URL, auth scheme,
identity, and secret. Create one with:

```bash
crm profile add
```

On a terminal, `add` with no flags runs an interactive wizard: it asks for the
server URL, infers the auth scheme from it (`*.dynamics.*` → OAuth, anything else →
NTLM), collects the identity fields (NTLM username/domain or OAuth tenant/client id)
and the secret, then saves the profile, stores the secret, runs a `WhoAmI` to
confirm, and activates it.

You don't even have to run it first: the **first time you run any connection command
with no profile configured**, the CLI launches this wizard for you automatically (on
a terminal). Under `--json` or a non-interactive shell it skips the wizard and errors
cleanly, telling you to run `crm profile add`.

## Non-interactive (scripting / CI)

Pass flags instead of answering prompts.

**On-prem (NTLM):**

```bash
crm profile add \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --password "$SECRET" \
    --name prod
```

**Online / Dataverse (OAuth):**

```bash
crm profile add \
    --url https://contoso.crm.dynamics.com \
    --tenant-id <aad-tenant-id> --client-id <app-registration-id> \
    --password "$CLIENT_SECRET" \
    --name online
```

See [Configure & switch](configure.md) for the full NTLM vs OAuth field reference and
day-to-day profile management, and [how-to: profile](../how-to/profile.md) for every
flag.
````

- [ ] **Step 2: Extend `docs/getting-started/configure.md`**

Keep the existing file as-is (it already documents NTLM/OAuth fields, default
solution/prefix, and secret storage). Append this section at the end, before any
trailing `See [How-to: profile]` line if present (otherwise at the very end):

````markdown
## Switching and managing profiles

You can keep several profiles and switch the active one:

```bash
crm profile list                 # show all profiles; the active one is marked
crm profile use online           # make "online" the active profile
crm profile edit prod            # change saved fields
crm profile set-password --profile prod   # replace the stored secret
crm profile rm old               # delete a profile
```

Commands use the active profile unless you pass `--profile <name>` for a single run.
See [how-to: profile](../how-to/profile.md) for every flag.
````

- [ ] **Step 3: Build and confirm**

Run: `cd "$WT" && ~/wip/projects/crm/.venv/bin/mkdocs build 2>&1 | tail -10 && test -f site/getting-started/add-profile/index.html && grep -q "Switching and managing" site/getting-started/configure/index.html && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
cd "$WT"
git add docs/getting-started/add-profile.md docs/getting-started/configure.md
git commit -m "docs: rework add-profile page and add profile-switching to configure"
```

---

## Task 6: Verify + Completion + Skill pages

**Files:**
- Create: `docs/getting-started/verify.md`
- Create: `docs/getting-started/completion.md`
- Create: `docs/getting-started/skill.md`

- [ ] **Step 1: Write `docs/getting-started/verify.md`**

````markdown
# Verify it works

After [adding a profile](add-profile.md), confirm the connection.

```bash
crm connection whoami
```

Prints the authenticated user id and organization. If you see them, you're
connected. A deeper check:

```bash
crm connection test      # round-trips a request to the Web API
crm connection doctor    # diagnoses URL, auth, and API-version issues
crm connection status    # shows the active profile and target
```

If any of these fail — a 401, a hang, or a "VPN down?" style message — see
[Troubleshooting](troubleshooting.md).
````

- [ ] **Step 2: Write `docs/getting-started/completion.md`**

````markdown
# Tab completion (optional)

Tab-completion for `crm` in bash, zsh, fish, or PowerShell.

```bash
crm completion install --shell zsh
```

This writes the completion script under `~/.crm/completion/` and prints **one line**
to add to your shell startup file. It never edits the file for you — copy the printed
line yourself, then restart your shell:

- **zsh** → `~/.zshrc`: `source ~/.crm/completion/crm.zsh`
- **bash** → `~/.bashrc`: `source ~/.crm/completion/crm.bash`
- **fish** → `~/.config/fish/config.fish`: `source ~/.crm/completion/crm.fish`
- **PowerShell** → `$PROFILE`: `. ~/.crm/completion/crm.ps1` (requires
  `--shell powershell` — it can't be autodetected)

`--shell` defaults to autodetecting `$SHELL`. A later `crm self-update` regenerates
the cached script automatically. See
[how-to: completion](../how-to/completion.md) for `--path`, `show`, and the
"why a cached file, not `eval`" note.
````

- [ ] **Step 3: Write `docs/getting-started/skill.md`**

````markdown
# Install the skill

`crm` ships an agent skill that teaches a coding agent how to drive Dynamics 365.
Install it into your agent's skill directory:

```bash
crm skill install --target claude
```

`--target` is `claude | copilot | cursor` (default `claude`). This copies the
bundled skill tree (`SKILL.md` + `reference/*.md`) into the agent's skill directory
and records the destination so [`crm self-update`](update.md) keeps it in sync as the
CLI upgrades.

Install to a custom directory with `--dest ./my-skills` (overrides `--target`); add
`--force` to overwrite an existing skill. See
[how-to: skill](../how-to/skill.md) for `path`, `uninstall`, and every flag.

Next: [use `/crm` with a coding agent](agent.md).
````

- [ ] **Step 4: Build and confirm**

Run: `cd "$WT" && ~/wip/projects/crm/.venv/bin/mkdocs build 2>&1 | tail -10 && for p in verify completion skill; do test -f site/getting-started/$p/index.html || { echo "MISSING $p"; exit 1; }; done && echo OK`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
cd "$WT"
git add docs/getting-started/verify.md docs/getting-started/completion.md docs/getting-started/skill.md
git commit -m "docs: add verify, completion, and skill getting-started pages"
```

---

## Task 7: Agent page + Troubleshooting

**Files:**
- Create: `docs/getting-started/agent.md`
- Create: `docs/getting-started/troubleshooting.md`

- [ ] **Step 1: Write `docs/getting-started/agent.md`**

````markdown
# Use /crm with a coding agent

Once you've [installed the skill](skill.md), a skill-aware agent (Claude Code,
Copilot CLI) can drive Dynamics 365 for you in plain language.

## How it triggers

The skill activates when your request mentions Dynamics 365, D365 CE, Dataverse, the
Web API, FetchXML, or on-prem CRM. The agent then runs `crm` commands with `--json`
and reads the structured output — no copy-pasting GUIDs.

## What it looks like

```text
You:  List the top 5 accounts by name from my D365 org.

Agent:  Running: crm query account --top 5 --select name --order-by name --json
        Here are the 5 accounts:
          1. Adventure Works
          2. Coho Vineyard
          3. Contoso Ltd
          4. Fabrikam, Inc.
          5. Northwind Traders

You:  Create a new account called "Tailspin Toys".

Agent:  Running: crm entity create account --data '{"name":"Tailspin Toys"}' --json
        Created account Tailspin Toys (id 8a1c…).
```

## Why `--json`

Every `crm` command emits a structured envelope under `--json`, so the agent reads
results deterministically instead of scraping human text. Use `--dry-run` to have the
agent preview a mutation before it runs.

Prerequisites: a working [profile](add-profile.md) (the agent uses your active
connection) and the [installed skill](skill.md).
````

- [ ] **Step 2: Write `docs/getting-started/troubleshooting.md`**

````markdown
# Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Install blocked; SmartScreen / Defender ASR / AppLocker flags the binary | The prebuilt binary is unsigned. Use the isolated [`uv tool install`](install.md#uv-tool-install-isolated) path, which runs through your trusted Python instead of a standalone executable. |
| On-prem commands hang or "can't reach server" | VPN is down. The on-prem org is only reachable on the corporate network — any HTTP response (including 401/403) counts as reachable. Connect the VPN and retry. |
| Secret won't save to the keyring (WSL / headless) | `crm` falls back automatically to a `0600` plaintext entry in the profile file. Force it with `crm profile add --store-password-plaintext`. |
| `WhoAmI` returns 401 / 403 | Wrong identity, or (OAuth) the app registration has no **application user** with a security role in Dynamics. Re-check credentials with `crm profile edit` and the role assignment in the org. |
| On-prem returns HTTP 501 for v9.2 | On-prem caps at v9.1. Omit `--api-version` and the CLI auto-steps-down. |

Still stuck? Run `crm connection doctor` for a guided diagnosis, or open an issue at
[github.com/Gharib89/crm](https://github.com/Gharib89/crm/issues).
````

- [ ] **Step 3: Build and confirm**

Run: `cd "$WT" && ~/wip/projects/crm/.venv/bin/mkdocs build 2>&1 | tail -10 && test -f site/getting-started/agent/index.html && test -f site/getting-started/troubleshooting/index.html && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
cd "$WT"
git add docs/getting-started/agent.md docs/getting-started/troubleshooting.md
git commit -m "docs: add agent-usage and troubleshooting getting-started pages"
```

---

## Task 8: Strict build gate + link audit

The final gate. All nav-referenced pages now exist, so `--strict` must pass clean.

**Files:** none (verification + fixes only)

- [ ] **Step 1: Run the strict build**

Run: `cd "$WT" && rm -rf site && ~/wip/projects/crm/.venv/bin/mkdocs build --strict 2>&1 | tail -30`
Expected: `INFO - Documentation built in ...` with **no WARNING lines** and exit 0. If any WARNING appears (broken link, page not in nav, missing reference), fix the offending file and re-run until clean.

- [ ] **Step 2: Confirm no stale `initialize` references remain in built docs**

Run: `cd "$WT" && git grep -n "getting-started/initialize" -- 'docs/getting-started' 'docs/index.md' mkdocs.yml`
Expected: **no output** (the only historical hits are under `docs/superpowers/` plans/specs, which are excluded from the build and must stay as history).

- [ ] **Step 3: Confirm every Getting Started page built**

Run:
```bash
cd "$WT"
for p in quickstart concepts install update add-profile configure verify completion skill agent troubleshooting; do
  test -f "site/getting-started/$p/index.html" && echo "ok $p" || echo "MISSING $p"
done
```
Expected: `ok` for all eleven; no `MISSING`.

- [ ] **Step 4: Spot-check the home page rendered the grid and buttons**

Run: `cd "$WT" && grep -q "md-button--primary" site/index.html && grep -q "grid cards" site/index.html && echo HOME_OK`
Expected: `HOME_OK`.

- [ ] **Step 5: Commit any fixes made during this task**

```bash
cd "$WT"
git status --short
# if Steps 1–2 required edits:
git add -- <fixed files>
git commit -m "docs: fix strict-build warnings in getting-started revamp"
```

- [ ] **Step 6: Push and open the PR**

```bash
cd "$WT"
git push -u origin docs/website-revamp
gh pr create --title "docs: revamp website — beginner getting-started + product home page" \
  --body "$(cat <<'EOF'
Reworks the docs site per `docs/superpowers/specs/2026-06-18-docs-website-revamp-design.md`:

- New beginner Getting Started journey (Quickstart, Concepts, Install, Update, Add a profile, Configure & switch, Verify, Tab completion, Install the skill, Use /crm with an agent, Troubleshooting).
- New home page: hero + feature-card grid + first-taste code (Material primitives, ITWorx brand).
- mkdocs.yml: added attr_list / md_in_html / pymdownx.tabbed; restructured Getting Started nav; updated llmstxt section list.
- Renamed getting-started/initialize.md → add-profile.md.

How-to pages are untouched (they remain the flag reference); Getting Started states the workflow and links down per the docs-in-sync rule. `mkdocs build --strict` passes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- IA / nav (Spec §1) → Task 1 (nav, llmstxt, rename). ✓
- Home page A (Spec §2) → Task 2. ✓
- Markdown extensions (Spec §2 addendum) → Task 1 Step 3. ✓
- Getting-started page contents (Spec §3): Quickstart+Concepts → Task 3; Install+Update → Task 4; Add-profile+Configure → Task 5; Verify+Completion+Skill → Task 6; Agent+Troubleshooting → Task 7. ✓ (all 11 pages covered)
- mkdocs.yml changes (Spec §4) → Task 1. ✓
- Verification (Spec) → Task 8 (strict build, grep, per-page existence). ✓
- Out of scope (no asciinema, no CLI changes) → honored; agent page uses text transcript. ✓

**Placeholder scan:** No TBD/TODO; every page has full body content; every command has expected output. ✓

**Type/name consistency:** Filenames consistent across nav (Task 1), inter-page links, and Task 8 existence checks (`quickstart, concepts, install, update, add-profile, configure, verify, completion, skill, agent, troubleshooting`). CSS class `.crm-hero` defined in Task 2 Step 2 matches its use in Task 2 Step 1. Build command identical across all tasks. ✓

**Gap check:** Concepts page links to `add-profile.md`/`configure.md` (exist by Task 5); agent.md links to skill.md/add-profile.md (exist by Task 6/5); all forward links resolve by the time Task 8 runs `--strict`. ✓
