# Customizations — apps, web resources, ribbon, forms, charts, dashboards, themes, reports, sitemap

UI-layer customization: model-driven apps and their sitemaps, web resources, entity
command-bar (ribbon) buttons, entity forms, charts, dashboards, application
themes, and custom reports. Groups: `app`, `webresource`, `ribbon`, `form`, `chart`,
`dashboard`, `theme`, `report`. Flags/choices: `crm <group> --help`.

## Model-driven apps — `app` (appmodule)

```bash
# create: --unique-name is publisher-prefixed, e.g. 'cwx_crmworx'.
crm --json app create --name CRMWorx --unique-name cwx_crmworx --if-exists skip

# add-components: APP_ID positional + repeatable --component 'kind:guid'.
# 'entity' is NOT a valid kind — tables surface via sitemap Entity= subareas.
crm --json app add-components <appmoduleid> \
    --component view:<savedqueryid> --component chart:<savedqueryvisualizationid>

# remove-components: inverse of add-components (RemoveAppComponents), same
# 'kind:guid' grammar + same vocabulary. --dry-run previews without calling.
crm --json app remove-components <appmoduleid> --component view:<savedqueryid>

# set-sitemap: SITEMAP_NAME positional is the sitemap's descriptive name
# (stored as sitemapname); --unique-name is the app's uniquename and sets
# sitemapnameunique to auto-associate the sitemap with that app.
crm --json app set-sitemap "CRMWorx Sitemap" --xml-file /tmp/sitemap.xml --unique-name cwx_crmworx

# build-sitemap: generates the SiteMapXml for you, then creates it via the same
# path as set-sitemap. Grammar: --area 'id[:Title]', --group 'areaId/groupId[:Title]',
# --subarea 'areaId/groupId:entity=<logical>[:Title]' (binds a table via Entity=).
# SubArea Ids are auto-allocated; refs/dup Ids are validated.
# crm --dry-run app build-sitemap ... prints the generated XML and does NOT POST.
crm --json app build-sitemap "CRMWorx Sitemap" \
    --area 'sales:Sales' --group 'sales/accounts:Customers' \
    --subarea 'sales/accounts:entity=account:Accounts' \
    --subarea 'sales/accounts:entity=contact' --unique-name cwx_crmworx
```

**On Unified Interface, tables are NOT added via `add-components`** — they surface
through the sitemap's `Entity=` subareas. A newly created entity is invisible in an
app until a subarea references it.

**Create→sitemap seam — carry the `appmoduleid`, don't re-create.** `app create`
publishes the app and then reads it back; in the publish-before-read window that
read-back can fail with a `meta.warnings` `app_lookup_error` **even though the app was
created**. The created `appmoduleid` is still in `data` — capture it and feed it to
`add-components`, `build-sitemap`, and teardown. Do **not** re-run `app create`: the app
already exists, a second create with a *new* `--unique-name` orphans a duplicate, and a
retry with the *same* name can hit `0x80050135` (duplicate) because the existence
pre-check rides that same not-yet-published read. Treat `app create` as create-once and
chain off its returned id.

**Teardown — use `app delete <name|id>`, not `entity delete appmodules`.** An app
won't delete while a dependent row holds a record-level FK to it: a bare
`entity delete appmodules <id>` fails `0x80048d21` ("referenced by another record"),
chiefly because an `appsetting` row still points at it. This block hits on **both**
on-prem and online — online too, despite the `appsetting` relationship's cascade-delete
metadata. `app delete` resolves the app (GUID / uniquename / display name), sweeps those
FK-blocking dependent rows first, then deletes the app; its `data` lists every dependent
removed (real run `dependents_deleted: [{entity, id}]`; `--dry-run` previews them under
`would_delete.dependents` and issues no DELETE). It **refuses a managed app** — uninstall
the parent solution instead.

## Web resources — `webresource` (HTML/JS/CSS/images)

```bash
# create: --file bytes are base64'd into `content`; webresourcetype is inferred from
# the extension (the real D365 option set, so .css=2 and 8 is Silverlight). An unknown
# extension without an explicit --type is rejected.
crm --json webresource create --name cwx_/scripts/ribbon.js --file ./ribbon.js --solution cwx_crmworx

# update <name>: plain PATCH of only the sent fields (content and/or display-name),
# resolved by name — NOT retrieve-merge.
crm --json webresource update cwx_/scripts/ribbon.js --file ./ribbon.js

# inspect
crm --json webresource get cwx_/scripts/ribbon.js
crm --json webresource list --custom-only

# use as a model-driven app icon
crm --json webresource create --name cwx_/icons/app.svg --file ./app.svg
crm --json app create --name CRMWorx --unique-name cwx_crmworx --icon-webresource cwx_/icons/app.svg
```

Both `create` and `update` honor `--solution` (`MSCRM.SolutionUniqueName`) and publish
after the write (`--no-publish` / global `--stage-only` suppress it; see
`reference/authoring.md`).

## Ribbon — `ribbon` (entity command-bar buttons)

The ribbon is stored as `RibbonDiffXml` and has **no first-class Web API write path**:
this group drives it through a solution zip + XML pipeline, so **every subcommand
except `export` works through the solution-zip pipeline — all require `--solution`.**
A button that runs a web resource needs that **web resource to already exist** —
create it first (above).

```bash
crm --json ribbon export account                 # one table's composed RibbonDiffXml
crm --json ribbon export --application           # application-wide ribbon (no ENTITY)
crm --json ribbon list account --solution cwx_crmworx
crm --json ribbon add-button account --solution cwx_crmworx ...
crm --json ribbon remove account --solution cwx_crmworx ...
```

**`ribbon export` — `ENTITY` or `--application`, not both.** Pass `ENTITY` for a
single table's ribbon, or `--application` / `-a` for the app-wide ribbon (the
commands not bound to any specific table). Passing both, or neither, is an error.
`--application` calls `RetrieveApplicationRibbon` (no parameters); the key in the
response is `CompressedApplicationRibbonXml`, distinct from the entity path's
`CompressedEntityXml`. Read-only.

This is why a cloned entity's ribbon does not come across (see the clone caveats in
`reference/metadata.md`) — there is no API write path to copy it.

**Ribbon writes are slow and synchronous.** Because every write rides the solution-zip
pipeline, `add-button` / `remove` run a **full solution import per call** — 60–120s with
no progress ticks. The command has not hung; **do not retry** a slow call (a second,
parallel attempt races the first import). Confirm the outcome afterward with
`ribbon list`.

## Forms — `form` (entity main forms / systemform)

```bash
crm --json form list cwx_ticket                                 # main forms only (the default)
crm --json form list cwx_ticket --all                           # every form type, not just main
crm --json form clone cwx_ticket "Information" --to cwx_ticketclone   # clone a named form to another table
crm --json form export cwx_ticket "Information" --output form.xml     # export a form's formxml
```

### Add / remove / move a field — first-class verbs

Use `form add-field`, `form remove-field`, and `form set-field` directly — no manual
FormXml editing required. The CLI resolves the control `classid` from live metadata and
PATCHes the `systemform` record.

```bash
crm --json form add-field cwx_ticket cwx_priority            # add to first section of first tab
crm --json form remove-field cwx_ticket cwx_priority         # remove; errors if absent
crm --json form set-field cwx_ticket cwx_priority \
    --tab "Details" --section "Status"                        # relocate; errors if not already present
```

**Publish gotcha — GET returns the published snapshot.** A plain `GET /systemforms`
returns the *published* FormXml, not the pending PATCH. The field edit is only visible
in the UI and on re-export **after `PublishAllXml` runs**. Always verify with a
re-export *after* publishing; a malformed splice publishes silently but the control is
absent from the exported XML.

```bash
crm --json form add-field cwx_ticket cwx_priority --publish   # PATCH + PublishAllXml in one call
```

**Unmapped types — fallback to hand-splice.** `add-field` maps the common
`AttributeType` values (text, numeric, money, datetime, boolean, option-set, lookup
families) to their control `classid` automatically. For a type with no mapped
constant (e.g. multi-select option sets, floating-point) the command **errors and
names the supported set** rather than guess an invalid classid — fall back to the
manual pipeline below for those.

**`--dry-run` support.** All three verbs honor the global `--dry-run` flag: reads
run for real (live metadata + form fetched), but no PATCH is issued and the response
carries `would_add` / `would_remove` / `would_move: true`.

### Manual splice — fallback for unmapped control types

Only needed when the attribute type has no mapped `classid` (see above):

```bash
crm --json form export cwx_ticket "Information" --output form.xml
# Copy the <control classid="…"> from a stock table that already carries that
# control type (e.g. account), splice a <cell> into the target <section>, then:
crm entity update systemforms <formid> --data-file form-update.json   # {"formxml":"…"}
crm solution publish --xml \
    '<importexportxml><entities><entity>cwx_ticket</entity></entities></importexportxml>'
```

Use `--data-file`, **not** inline `--data` — FormXml is quote-heavy and must be
JSON-escaped. Get `<formid>` from `form list`.

On Unified Interface a cloned/added form may need adding to the model-driven app's form
list to be visible.

## Charts — `chart` (savedqueryvisualization / userqueryvisualization)

Author charts headlessly instead of using the chart designer. System charts
(org-wide, `savedqueryvisualization`) are the default; `--user` targets user-owned
charts (`userqueryvisualization`), which have no `isdefault` flag and a
`userqueryvisualizationid` id field.

```bash
crm --json chart list contact                          # system charts (default)
crm --json chart list contact --user                   # user charts
crm --json chart get <id>                              # single chart, with its XML
crm --json chart delete <id> [--user]                  # delete
```

A chart carries two XML columns: `datadescription` (aggregate FetchXML, references
the host table) and `presentationdescription` (series/areas rendering XML). To
version a chart, capture both from `chart get` and recreate with `chart create`:

```bash
crm --json chart get <id> | jq -r '.data.datadescription' > c.data.xml
crm --json chart get <id> | jq -r '.data.presentationdescription' > c.pres.xml
crm --json chart create contact --name "By Method" \
    --data-description c.data.xml --presentation-description c.pres.xml
```

**Two mutually exclusive create modes.** XML mode needs **both**
`--data-description` and `--presentation-description`; web-resource mode is
`--web-resource <name|GUID>` (resolved to its `webresourceid`). Passing both modes,
or only one XML file, is a usage error.

**Server validates the XML.** The presentation XML's chart-area count must match the
data XML's category count, etc. — a malformed pair fails with a `400`
(`The number of chart areas must be equal to the number of categories.`). When in
doubt, start from a known-good chart captured via `chart get`.

**Publish + solution + dry-run, same contract as the metadata verbs.** `create`
runs `PublishAllXml` by default (`--no-publish` to stage); `--solution` /
`--require-solution` scope the write. Under `--dry-run`, `create` returns
`{_dry_run, would_create: {entity_set, body}}` with the resolved body (a
`--web-resource` name is resolved live first) and `delete` returns
`{_dry_run, would_delete: true, <id>}` — neither issues the write. To take a chart
*out* of a solution without deleting it, use `solution remove-component`.

## Dashboards — `dashboard` (systemform type=0)

Author organization-owned system dashboards headlessly instead of using the
dashboard designer. A dashboard is a `systemform` with `type = 0`; the verbs scope
every read to that type, so other form types never appear.

```bash
crm --json dashboard list                              # org dashboards (no formxml)
crm --json dashboard get <id>                          # single dashboard, with formxml
crm --json dashboard create --name "Sales" --formxml dash.xml
crm --json dashboard delete <id>
```

**The CLI does not author FormXml** — it posts the file verbatim. To version a
dashboard, capture its layout from `dashboard get` and recreate it:

```bash
crm --json dashboard get <id> | jq -r '.data.formxml' > dash.xml
crm --json dashboard create --name "Sales" --formxml dash.xml
```

**Interactive (type-10) dashboards are not API-creatable.** Passing `--interactive`
fails fast with a clear error rather than silently creating a standard dashboard —
author interactive-experience dashboards in the designer.

**Publish + solution + dry-run, same contract as the other customization verbs.**
`create` runs `PublishAllXml` by default (`--no-publish` to stage); `--solution` /
`--require-solution` scope the write. Under `--dry-run`, `create` returns
`{_dry_run, would_create: {entity_set, body}}` and `delete` returns
`{_dry_run, would_delete: true, formid}` — neither issues the write.

## Themes — `theme` (application branding)

Author product branding (colors, logo) as code. A theme is an ordinary `themes`
record; `publish` promotes one to the **active org-wide theme** via the
`PublishTheme` action. Verbs: `list`, `get`, `create`, `update`, `publish`.

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
**not** travel with a solution export, so there is no `--solution` flag and you
should not expect a theme to appear in a packaged solution or move across orgs with
one. Move branding between orgs by re-running `theme create`/`update`.

**`publish` sets the active theme org-wide** and the CLI has no inverse verb to
restore the previous one — capture the current default first (`theme list` →
the row with `isdefaulttheme: true`) so you can re-`publish` it to roll back.

**`--dry-run`** previews `create`/`update`/`publish` without writing
(`would_create` / `would_update` / `would_publish`); a `--logo` name is resolved
live first. There is no `theme delete` verb — drop a theme with
`entity delete themes <id>`.

## Reports — `report` (reports entity)

Register custom reports headlessly without the Report Wizard. Two kinds:
`create --body-file` uploads an SSRS RDL file; `create --url` registers an
external link report. Verbs: `list`, `get`, `create`, `set-category`, `delete`.

```bash
crm --json report list                                 # all reports (summary cols)
crm --json report get <id>                             # one report, body included
crm --json report create --name "Pipeline" --body-file pipeline.rdl
crm --json report create --name "Ext Dash" --url "https://example.com/dash"
crm --json report set-category <id> --category sales
crm --json report delete <id>
```

**`--org` makes a report org-wide by setting `ispersonal=false`** on the `reports`
record — this is the Web API path for org-wide visibility. The deprecated SDK
message `MakeAvailableToOrganizationReport` has no Web API binding and is never
used. Without `--org`, reports are personal (`ispersonal=true`).

**The CLI uploads RDL content verbatim** — it does not author or validate the
XML. Dataverse online only accepts RDLs using the fetch data provider; on-prem
v9.x uses the standard D365 data source. RDL authoring is out of scope.

**Reports are solution-aware.** `create` and `set-category` honor `--solution` /
`--require-solution` to scope the write to an unmanaged solution.

**`set-category` creates a `reportcategory` record** (categorycode 1–4: sales,
service, marketing, administrative). A report can belong to multiple areas.
Capture the returned `reportcategoryid` to remove a category later:

```bash
crm --json report set-category <id> --category sales   # → data.reportcategoryid
crm entity delete reportcategories <reportcategoryid> --yes
```

**`--dry-run`** previews `create` without writing — returns
`{_dry_run, would_create: {entity_set, body}}`.

## Decommission — deleting UI components

`app` and `ribbon` have **no `delete` verb** — delete through the generic
`entity delete <set>`, or drop the whole solution to cascade. `webresource` has a
first-class `crm webresource delete <name|id> --yes`. Order matters; the platform
enforces the dependencies and the error code names the one you hit:

- **Web resource referenced by a ribbon button** — remove the button first
  (`ribbon remove …`), else the delete fails `0x8004f01f` (still referenced). Use
  `crm webresource delete <name|id> --check-dependencies` to preview blockers before
  attempting the delete. After clearing the button, retry:
  `crm webresource delete <name|id> --yes`.
- **Model-driven app** — use `app delete <name|id> --yes`: it sweeps the FK-blocking
  dependent rows (chiefly `appsetting`) before deleting the app, which a bare
  `entity delete appmodules <id>` does not — that fails `0x80048d21` (referenced by
  another record) on both on-prem and online. It refuses a managed app. Deleting the
  **containing unmanaged solution** also cascades the app away and remains a valid route
  when you're dropping the whole solution anyway.
- **Custom table** — `metadata delete-entity <logical> --yes` cascades its columns,
  relationships, views, and forms in one shot; delete the table before the global option
  sets and publisher it depended on.
