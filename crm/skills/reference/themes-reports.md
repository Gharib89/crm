# Themes & reports — `theme`, `report`

Author application branding as code, and register custom reports without the
Report Wizard. Groups: `theme`, `report`. Flags/choices: `crm <group> --help`.

## Themes — `theme` (application branding)

A theme is an ordinary `themes` record; `publish` promotes one to the **active
org-wide theme** via the `PublishTheme` action. Verbs: `list`, `get`, `create`,
`update`, `publish`.

```bash
crm --json theme list                                  # all themes (summary cols)
crm --json theme get <id>                              # one theme, full branding
crm --json theme create --name "Corporate Blue" \
    --set maincolor=#0066cc --set navbarbackgroundcolor=#002050
crm --json theme update <id> --set maincolor=#ff0000   # change a color
crm --json theme publish <id>                          # make it the active org theme
```

**Branding via `--set FIELD=VALUE` (repeatable).** Colors are `#rrggbb` strings on
columns like `maincolor`, `navbarbackgroundcolor`, `navbarshelfcolor`,
`headercolor`, `globallinkcolor`, `selectedlinkeffect`, `processcontrolcolor`,
`pageheaderbackgroundcolor`, `panelheaderbackgroundcolor`. `--set` keys are used
verbatim and VALUEs parse as JSON with a raw-string fallback. `--logo <name|GUID>`
binds a web resource as the logo (create it first with `webresource create`).

**Themes are NOT solution-aware.** A theme is not a solution component — it does
**not** travel with a solution export, so there is no `--solution` flag (the
exception to the solution-scoped rule) and you should not expect a theme to appear
in a packaged solution or move across orgs with one. Move branding between orgs by
re-running `theme create`/`update`.

**`publish` sets the active theme org-wide** and the CLI has no inverse verb to
restore the previous one — capture the current default first (`theme list` →
the row with `isdefaulttheme: true`) so you can re-`publish` it to roll back.

**`--dry-run`** previews `create`/`update`/`publish` without writing
(`would_create` / `would_update` / `would_publish`); a `--logo` name is resolved
live first. There is no `theme delete` verb — drop a theme with
`entity delete themes <id>`.

## Reports — `report` (reports entity)

Two kinds: `create --body-file` uploads an SSRS RDL file; `create --url` registers
an external link report. Verbs: `list`, `get`, `create`, `set-category`, `delete`.

```bash
crm --json report list                                 # all reports (summary cols)
crm --json report get <id>                             # one report, body included
crm --json report create --name "Pipeline" --body-file pipeline.rdl --solution ContosoCore
crm --json report create --name "Ext Dash" --url "https://example.com/dash" --solution ContosoCore
crm --json report set-category <id> --category sales --solution ContosoCore
crm --json report delete <id>
```

**`--org` makes a report org-wide by setting `ispersonal=false`** on the `reports`
record — this is the Web API path for org-wide visibility. The deprecated SDK
message `MakeAvailableToOrganizationReport` has no Web API binding and is never
used. Without `--org`, reports are personal (`ispersonal=true`).

**The CLI uploads RDL content verbatim** — it does not author or validate the
XML. Dataverse online only accepts RDLs using the fetch data provider; on-prem
v9.x uses the standard D365 data source. RDL authoring is out of scope.

**`create` and `set-category` are solution-scoped** (see SKILL.md); `delete`
takes no `--solution`.

**`set-category` creates a `reportcategory` record** (categorycode 1–4: sales,
service, marketing, administrative). A report can belong to multiple areas.
Capture the returned `reportcategoryid` to remove a category later:

```bash
crm --json report set-category <id> --category sales --solution ContosoCore  # → data.reportcategoryid
crm entity delete reportcategories <reportcategoryid> --yes
```

**`--dry-run`** previews `create` without writing — returns
`{_dry_run, would_create: {entity_set, body}}`.
