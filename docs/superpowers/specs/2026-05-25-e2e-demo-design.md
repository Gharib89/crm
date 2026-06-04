# E2E Demo + MkDocs Reference Site

**Date:** 2026-05-25
**Goal:** Execute a realistic IT-services customization scenario against the live Contoso
D365 CE on-prem server, capture real output at every step, and publish a complete
MkDocs Material reference site (scenario walkthrough + full command reference).

---

## 1. Scope

Two deliverables:

| # | Deliverable | Location |
|---|-------------|----------|
| 1 | Live scenario execution (6 phases) against `http://internalcrm.contoso.local/Contoso` | terminal output captured per phase |
| 2 | MkDocs Material site | `docs/` + `mkdocs.yml`; built to `site/` |

The scenario is the narrative spine of the docs — every scenario command becomes a
live example on the corresponding doc page.

---

## 2. Server & credentials

| Setting | Value |
|---------|-------|
| `CRM_BASE_URL` | `http://internalcrm.contoso.local/Contoso` |
| `CRM_USERNAME` | `contoso\crmadmin` |
| `CRM_AUTH` | `ntlm` |
| `CRM_API_VERSION` | `v9.1` |
| `CRM_PASSWORD` | env var (never written to docs) |

No profile file needed — CLI resolves credentials from env vars directly.

---

## 3. Scenario — Contoso-IT client request

**Narrative:** Contoso-IT is an IT services company running D365 CE on-prem. They need
to track internal **IT Projects** and their associated **Service Tickets**. Each
ticket has a priority tier and a status. Projects have a name, start date, and budget.
Management wants to report on open tickets per project.

### Custom schema to create

| Artifact | Logical name | Type |
|----------|-------------|------|
| IT Project entity | `new_itproject` | custom entity |
| Service Ticket entity | `new_serviceticket` | custom entity |
| Ticket Priority option set | `new_ticketpriority` | global option set |
| Budget attribute | `new_budget` on `new_itproject` | Money |
| Start Date attribute | `new_startdate` on `new_itproject` | DateTime |
| Project lookup on ticket | `new_projectid` on `new_serviceticket` | Lookup (1:N) |
| Priority attribute | `new_priority` on `new_serviceticket` | Picklist (global OS) |
| Project → Ticket relationship | `new_itproject_servicetickets` | 1:N |

---

## 4. Execution phases

### Phase 0 — Bootstrap & connect
Commands: `crm init --template`, `crm connection connect`, `crm connection test`,
`crm connection whoami`

Success criterion: WhoAmI returns a valid `UserId`.

### Phase 1 — Explore existing schema
Commands: `crm metadata entities`, `crm metadata entity account`,
`crm query count account`, `crm metadata list-actions`, `crm metadata list-functions`

Success criterion: entity list returned; account record count > 0.

### Phase 2 — Create schema
Commands (in order):
1. `crm metadata create-entity --schema-name new_ItProject --display "IT Project"`
2. `crm metadata add-attribute new_itproject --kind money --schema-name new_Budget --display "Budget"`
3. `crm metadata add-attribute new_itproject --kind datetime --schema-name new_StartDate --display "Start Date" --format DateOnly`
4. `crm metadata create-optionset --name new_ticketpriority --display "Ticket Priority" --option "1:Low" --option "2:Medium" --option "3:High" --option "4:Critical"`
5. `crm metadata create-entity --schema-name new_ServiceTicket --display "Service Ticket"`
6. `crm metadata add-attribute new_serviceticket --kind memo --schema-name new_Description --display "Description"`
7. `crm metadata create-one-to-many --schema-name new_itproject_servicetickets --referenced-entity new_itproject --referencing-entity new_serviceticket --lookup-schema new_ProjectId --lookup-display "IT Project"`
8. `crm metadata add-attribute new_serviceticket --kind picklist --schema-name new_Priority --display "Priority" --optionset-name new_ticketpriority`
9. `crm solution publish-all`

Note: OData entity set names (`new_itprojects`, `new_servicetickets`) are confirmed
after entity creation via `crm metadata entity new_itproject --json | jq .EntitySetName`.

Success criterion: entities appear in `crm metadata entities`; option set visible in
`crm metadata get-optionset new_ticketpriority`.

### Phase 3 — Record CRUD
Commands:
1. Create 2 IT Project records via `crm entity create new_itprojects`
2. Create 3 Service Ticket records via `crm entity create new_servicetickets`
3. Associate tickets to projects via the lookup (set via `--data` on create or
   `crm entity set-lookup`)
4. `crm entity get new_itprojects <guid>`
5. `crm entity update new_itprojects <guid>` — change budget
6. `crm entity upsert new_servicetickets` — upsert a 4th ticket

Success criterion: records retrievable; lookup populated.

### Phase 4 — Query & export
Commands:
1. `crm query odata new_servicetickets --filter "new_priority eq 3" --select new_title,new_priority`
2. `crm query odata new_itprojects --expand new_itproject_servicetickets --top 5`
3. `crm query count new_serviceticket`
4. `crm query fetchxml new_servicetickets --file docs/demo/tickets-by-project.xml`
5. `crm data export new_servicetickets -o docs/demo/tickets.csv --select new_title,new_priority`

Success criterion: query returns ticket rows; CSV written to disk.

### Phase 5 — Solution & publish
Commands:
1. `crm solution list`
2. `crm solution info Default`
3. `crm solution export Default -o docs/demo/Default.zip`
4. `crm solution publish-all`

Success criterion: solution ZIP written; publish returns 204.

### Phase 6 — Batch & workflow
Commands:
1. `crm batch docs/demo/batch-create-tickets.json` — bulk-create 3 tickets in one call
2. `crm workflow list`
3. `crm async list --top 5`

Success criterion: batch returns all-200 results; async list non-empty.

---

## 5. MkDocs site structure

```
mkdocs.yml
docs/
  index.md                  # overview, install, quick-start
  scenario/
    index.md                # Contoso-IT narrative intro + entity diagram
    phase-0-connect.md
    phase-1-explore.md
    phase-2-schema.md
    phase-3-records.md
    phase-4-query.md
    phase-5-solution.md
    phase-6-batch.md
  reference/
    connection.md
    entity.md
    metadata.md
    query.md
    solution.md
    workflow.md
    action.md
    batch.md
    data.md
    async.md
    session.md              # covers session + repl + init
```

### 5.1 mkdocs.yml key settings

```yaml
theme:
  name: material
  palette:
    - scheme: default
      toggle: {icon: material/brightness-7, name: Switch to dark mode}
    - scheme: slate
      toggle: {icon: material/brightness-4, name: Switch to light mode}
  features:
    - navigation.tabs
    - navigation.sections
    - content.code.copy

markdown_extensions:
  - admonition
  - pymdownx.superfences
  - pymdownx.tabbed:
      alternate_style: true
  - tables
```

### 5.2 Reference page structure (per command group)

Each `reference/<group>.md` follows this template:

```
# <Group>

One-sentence purpose.

## Subcommands

### <subcommand>

**Syntax:** `crm <group> <subcommand> [OPTIONS]`

| Flag | Type | Default | Description |
|------|------|---------|-------------|
...

**Example**
\`\`\`bash
crm <group> <subcommand> ...
\`\`\`

**Output**
\`\`\`
<real captured output>
\`\`\`
```

### 5.3 Scenario page structure

Each `scenario/phase-N-*.md` follows:

```
# Phase N — <Title>

> **Story beat:** one sentence connecting this phase to the Contoso-IT narrative.

## Commands run

Step-by-step with command block + real captured output per command.

## What we learned

1-3 bullet takeaways relevant to a developer using the CLI.
```

---

## 6. Tooling changes

| Change | Detail |
|--------|--------|
| Add `mkdocs-material` | `pip install mkdocs-material` + `docs` extra in `setup.cfg` |
| `mkdocs.yml` | New file at repo root |
| `docs/demo/` | Supporting artifacts: FetchXML file, batch JSON file |
| `site/` | Build output — add to `.gitignore` |

---

## 7. Success criteria (overall)

- [ ] All 6 phases execute against Contoso without error
- [ ] `mkdocs build` completes with zero warnings
- [ ] Every CLI subcommand appears in at least one reference page
- [ ] Every scenario command has real captured output embedded
- [ ] `site/index.html` opens correctly in a browser (offline)

---

## 8. Out of scope

- `action invoke` / `action function` — no custom actions registered on Contoso; reference page only (no live example)
- `workflow activate/run` — no workflows on Contoso by default; document the commands with synthetic example
- `session` internals — document surface only
- REPL tab completion — not capturable; note in reference page
- Deploying to GitHub Pages (`mkdocs gh-deploy`) — out of scope for this spec
