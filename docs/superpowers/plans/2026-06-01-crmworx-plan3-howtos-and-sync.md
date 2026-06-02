# CRMWorx Guide — Plan 3: How-tos + Skill/CLI Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the per-group how-to pages from the live run, document the skill↔CLI sync workflow, reconcile the existing skill drift, and verify the whole docs site builds clean.

**Architecture:** Pure documentation + a skill resync. How-to pages distil real commands captured in `docs/guides/crmworx-walkthrough.md` (Plan 2) into reusable recipes. The skill source of truth `crm/skills/SKILL.md` is reinstalled to the active Claude location, eliminating the drift where the active copy lacks the destructive-ops section.

**Tech Stack:** MkDocs, the `crm skill` command, diff/patch.

**Prerequisites:** Plans 1 and 2 complete (scaffold + transcribed walkthrough exist), **and the CRMWorx interface plan** (`2026-06-01-crmworx-interface.md`) complete — its §6–§13 interface sections and the new `crm view` / `crm app` command groups must already exist on `main`.

**Execution order:** This plan runs **last** in the CRMWorx series: Plan 1 (scaffold) → Plan 2 (live-run walkthrough) → interface plan (interface build + `view`/`app` commands) → **Plan 3 (this — distill how-tos + sync skill)**. Plan 3 reads the *finished* walkthrough and syncs `SKILL.md` to the *full* command set, so running it before the interface work would leave its output immediately stale (missing view/app how-tos + SKILL coverage) and force a re-run.

---

## File structure

- Modify: `docs/how-to/{connection,entity,query,metadata,solution,data,action}.md`
- Create: `docs/how-to/view.md`, `docs/how-to/app.md` (the interface command groups)
- Modify: `mkdocs.yml` (add the two new how-to pages to the `How-to` nav)
- Modify: `docs/contributing/skill-and-cli.md`
- Modify: `crm/skills/SKILL.md` (document the new `crm view` / `crm app` / `solution create-publisher` / `solution create` commands — see Task 2)
- Modify (resync target): `~/.claude/skills/crm/SKILL.md`

### Task 1: Fill the seven how-to pages from the walkthrough

**Files:**
- Modify: `docs/how-to/connection.md`, `entity.md`, `query.md`, `metadata.md`, `solution.md`, `data.md`, `action.md`

- [ ] **Step 1: Write each how-to from real captured commands**

For each group, replace the stub with 2–4 task recipes drawn from the CRMWorx run.
Each recipe is a `## <task>` heading, the exact command, and a one-line note on output.
Pull the commands verbatim from `docs/guides/crmworx-walkthrough.md` so they match
reality. Example for `docs/how-to/metadata.md`:

```markdown
# How-to: metadata

Recipes for schema work, taken from the CRMWorx build. See the
[CLI reference](../reference/cli.md) for every flag.

## Create a global option set (idempotent)

```bash
crm --json metadata create-optionset --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical --if-exists skip
```
`--if-exists skip` makes re-runs a no-op.

## Add a picklist column bound to that option set

```bash
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Priority --display "Priority" --optionset-name cwx_priority --if-exists skip
```

## Create a 1:N relationship (adds a lookup on the N side)

```bash
crm --json metadata create-one-to-many --schema-name cwx_sla_cwx_ticket \
  --referenced-entity cwx_sla --referencing-entity cwx_ticket \
  --lookup-schema cwx_SLA --lookup-display "SLA Policy" --if-exists skip
```
```

Do the equivalent for the other six groups (connection: whoami/connect/profiles;
entity: create/update/lookup-bind; query: odata/fetchxml; solution: lead with
`create-publisher` + `create` — the zero-web-UI CRMWorx prerequisite (#34), then
components/export/publish-all; data: export; action: function). `session` has no
dedicated page — fold
`session info`/`history` into the connection page or the walkthrough coverage table.

- [ ] **Step 2: Write the two new interface how-to pages**

The interface plan added the `crm view` and `crm app` command groups (walkthrough §13)
and the live primitive-driven interface build (§6–§12). Create two new pages, drawing
commands verbatim from those sections.

`docs/how-to/view.md`:

```markdown
# How-to: view

Create system views (savedquery). Taken from the CRMWorx interface build (§6, §13).

## Create an active-records public view

```bash
crm --json view create cwx_ticket --name "Active Tickets" --otc <OTC> \
  --column "cwx_name:220" --column "cwx_priority:120" --column "cwx_customerid:180" \
  --order cwx_name --filter-active --if-exists skip
```
Get `<OTC>` (ObjectTypeCode) from `crm --json metadata entity cwx_ticket`. `--filter-active`
restricts to `statecode=0`; `--if-exists skip` makes re-runs a no-op.
```

`docs/how-to/app.md`:

```markdown
# How-to: app

Create model-driven apps (appmodule) and bind components. From the CRMWorx build (§11, §13).

## Create the app

```bash
crm --json app create --name CRMWorx --unique-name cwx_crmworx \
  --description "CRMWorx IT ticketing" --if-exists skip
```

## Bind entities, views, forms, charts, dashboards

```bash
crm --json app add-components <appmoduleid> \
  --component entity:<cwx_ticket-guid> --component view:<savedqueryid> \
  --component chart:<savedqueryvisualizationid> --component form:<formid>
```
`<appmoduleid>` comes from `app create`. Component kinds: entity|view|chart|form|dashboard|bpf|sitemap.

## Attach a sitemap

```bash
crm --json app set-sitemap "CRMWorx Sitemap" --xml-file sitemap.xml
```
```

- [ ] **Step 3: Add the two pages to the mkdocs nav**

In `mkdocs.yml`, under the `How-to:` nav block, add (after `action: how-to/action.md`):
```yaml
      - view: how-to/view.md
      - app: how-to/app.md
```
(`mkdocs build --strict` warns about any docs page absent from the nav, so this is required.)

- [ ] **Step 4: Build strict**

Run: `mkdocs build --strict`
Expected: no warnings.

- [ ] **Step 5: Commit**

```bash
git add docs/how-to/ mkdocs.yml
git commit -m "docs: fill how-to recipes from the CRMWorx run (incl. view/app)"
```

### Task 2: Reconcile the agent-skill drift

**Files:**
- Read: `crm/skills/SKILL.md` (source of truth), `.claude/skills/crm/SKILL.md` (active, drifted)
- Modify: `~/.claude/skills/crm/SKILL.md` via `crm skill install --force`

- [ ] **Step 1: Confirm the drift**

```bash
diff crm/skills/SKILL.md .claude/skills/crm/SKILL.md
```
Expected: the active copy is missing the "Destructive operations — `--yes` confirm
contract" section (and possibly run-exposed updates). Confirm the source-of-truth copy
is the more complete one.

- [ ] **Step 2: Document the new commands + any run-exposed behavior in the source of truth**

The interface plan's Phase B added the `crm view` and `crm app` command groups — these
are **new CLI surface** the agent skill must describe. Add a short section to
`crm/skills/SKILL.md` covering `view create` (savedquery; needs `--otc`, repeatable
`--column logical:width`, `--filter-active`) and `app create` / `app add-components`
(appmodule; component kinds entity|view|chart|form|dashboard|bpf|sitemap) — mirror the
terseness of the existing command entries.

Likewise add the new `solution` verbs (issue #34, now on `main`): `solution
create-publisher` (publishers; `--prefix` 2-8 alnum / letter-first / not `mscrm`,
`--option-value-prefix` 10000-99999) and `solution create` (`--publisher` xor
`--publisher-id`; both auto-wire `publisher_prefix` / `default_solution` into a named
profile, `--no-set-default` to opt out — see [ADR-0002](../../adr/0002-create-verbs-auto-wire-profile.md)).

Also, if Plan 2 or the interface run surfaced behavior not in `crm/skills/SKILL.md`
(e.g. a corrected `@odata.bind` property-name pattern, the `GlobalOptionSet@odata.bind`
MetadataId requirement, or the LayoutXml `object="<OTC>"` quirk), add a one-line note.

- [ ] **Step 3: Reinstall the skill to the active Claude location**

```bash
crm --json skill install --target claude --force
diff crm/skills/SKILL.md .claude/skills/crm/SKILL.md && echo "IN SYNC"
```
Expected: `IN SYNC` (the diff is now empty).

- [ ] **Step 4: Commit any source-of-truth change**

```bash
git add crm/skills/SKILL.md
git commit -m "docs(skill): sync SKILL.md with CRMWorx run findings" || echo "no source change"
```
(The `~/.claude/...` copy lives outside the repo; only the source-of-truth commit matters.)

### Task 3: Write the skill ↔ CLI sync + bug-loop contributing page

**Files:**
- Modify: `docs/contributing/skill-and-cli.md`

- [ ] **Step 1: Replace the stub with the real workflow**

```markdown
# Keeping the agent skill in sync with the CLI

## Source of truth

`crm/skills/SKILL.md` is the canonical agent skill. The package ships it
(`package_data` in `setup.py`) and `crm skill install` copies it into an agent's
skill directory:

| Target | Destination |
| --- | --- |
| `claude` | `~/.claude/skills/crm/SKILL.md` |
| `copilot` | `~/.copilot/skills/crm/SKILL.md` |
| `cursor` | `~/.cursor/rules/crm/SKILL.md` |

```bash
crm skill install --target claude --force
```

## When the CLI changes

After adding or changing a command, update `crm/skills/SKILL.md`, then reinstall with
`--force`. To detect drift:

```bash
diff crm/skills/SKILL.md ~/.claude/skills/crm/SKILL.md
```

## The bug loop used to build CRMWorx

While building the walkthrough, defects were handled with a hybrid policy:

- **Trivial / single-function:** write a failing test in `crm/tests/`, fix in `crm/`,
  re-run, continue — the fix lands in the same session.
- **Larger:** file a triaged GitHub issue with a minimal repro, work around, continue.

Issues filed during the run are tracked in
[GitHub Issues](https://github.com/Gharib89/crm/issues).
```

- [ ] **Step 2: Build strict + commit**

```bash
mkdocs build --strict
git add docs/contributing/skill-and-cli.md
git commit -m "docs: document skill/CLI sync workflow and bug loop"
```

### Task 4: Final whole-site verification

**Files:**
- None (verification only)

- [ ] **Step 1: Strict build, zero warnings**

Run: `mkdocs build --strict 2>&1 | tee /tmp/mkdocs.log; grep -c WARNING /tmp/mkdocs.log`
Expected: `0` warnings; final line `Documentation built in ...`.

- [ ] **Step 2: Confirm every nav page resolves**

Run: `for f in index getting-started/install getting-started/configure guides/crmworx-walkthrough how-to/connection how-to/entity how-to/query how-to/metadata how-to/solution how-to/data how-to/action how-to/view how-to/app reference/cli contributing/skill-and-cli; do test -e "site/$f/index.html" -o -e "site/$f.html" && echo "ok $f" || echo "MISSING $f"; done`
Expected: every line starts with `ok` (includes the new `how-to/view` + `how-to/app` interface pages).

- [ ] **Step 3: Confirm coverage table lists all groups + no stub markers remain**

Run: `grep -rl "Status:.*scaffold" docs/ || echo "no stubs remain"`
Expected: `no stubs remain`.

- [ ] **Step 4: Final commit (if any verification touched files)**

```bash
git status --porcelain
```
If clean, nothing to commit. Otherwise stage the fix and commit with a descriptive message.

## Self-review notes

- Plan 3 produces no server mutations except `crm skill install` (local file copy).
- The `~/.claude/skills/crm/SKILL.md` resync is intentionally outside git; only the
  in-repo source of truth is versioned.
- Success criteria from the spec satisfied here: strict build clean, all groups covered,
  skill resynced + drift reconciled, no remaining stubs.
