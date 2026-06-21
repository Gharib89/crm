# Customizations — apps, web resources, ribbon, forms, charts, dashboards, themes, reports, sitemap

UI-layer customization: model-driven apps and their sitemaps, web resources, entity
command-bar (ribbon) buttons, entity forms, charts, dashboards, application
themes, and custom reports. Groups: `app`, `sitemap`, `webresource`, `ribbon`, `form`, `chart`,
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

## SiteMap live editor — `sitemap`

Surgically edit an **existing** sitemap's navigation tree in place over the
read-modify-write (RMW) seam: GET `sitemaps({id})?$select=sitemapxml` → mutate the
parsed XML tree → PATCH → publish → T3 read-back. Complements `app build-sitemap` /
`app set-sitemap` which POST a whole new SiteMapXml.

**Find the sitemap GUID first:**

```bash
crm --json query odata sitemaps --select sitemapname,sitemapid
# → data[].sitemapid is the SITEMAP_ID positional arg
```

**The four verbs:**

```bash
# Add an Area (id unique across all node ids; publisher-prefix recommended)
crm --json sitemap add-area <SITEMAP_ID> --id cwx_sales --title "Sales" --publish

# Add a Group under an Area
crm --json sitemap add-group <SITEMAP_ID> \
    --area cwx_sales --id cwx_grp --title "Customers" --publish

# Add a SubArea — exactly one of --entity / --url / --dashboard
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_grp --id cwx_accts --entity account --publish
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_grp --id cwx_page --url "/WebResources/cwx_.html" --publish
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_grp --id cwx_dash --dashboard <guid> --publish

# Remove (or soft-delete with --comment-out)
crm --json sitemap remove-node <SITEMAP_ID> --id cwx_accts --publish
crm --json sitemap remove-node <SITEMAP_ID> --id cwx_sales --comment-out --publish
```

**Workflow-level gotchas the `--help` doesn't surface:**

- **Exactly one content binding per SubArea.** `--entity`, `--url`, and `--dashboard`
  are mutually exclusive. Passing more than one, or none, is a usage error.
- **`--entity` is validated live.** A logical name that doesn't exist in the org is
  rejected before the PATCH — a dangling `Entity=` would silently hide the SubArea.
- **There is no SubArea `WebResource` attribute.** A web-resource-backed SubArea uses
  `--url` (pointing at the web resource URL path). The `$webresource:` prefix is
  the `--icon` directive only, not a content binding.
- **`ResourceId` and `IntroducedVersion` are never written.** These are
  platform-owned — new nodes get only `Title`; the CLI never touches them.
- **Every new node Id is unique across the whole document** (all Area / Group /
  SubArea Ids), matching `build_sitemapxml` — this keeps `remove-node --id`
  unambiguous, since it targets by Id across all node types.
- **`remove-node` cascades** — removing an Area or Group that has descendants emits a
  `meta.warnings` cascade advisory. Use `--dry-run` first to preview the subtree.
- **`--comment-out`** replaces the node with a well-formed XML comment instead of
  deleting it — a reversible soft-delete. The commented node is not a live node, so
  its id frees up for reuse (uniqueness checks scan live nodes only).

**Publish-gated T3 read-back — the key gotcha:**

A Web API GET for `sitemapxml` returns the **published** layer, not the staged edit.
An edit written with `--no-publish` will not appear in a re-fetch until
`PublishAllXml` runs — on on-prem v9.x especially, a read-back before publish
false-negatives. `--publish` (the default) runs `PublishAllXml` + a T3 read-back
inside the verb itself.

**Do NOT chain `--no-publish` edits to the same sitemap.** Each verb re-reads
`sitemapxml` before mutating, so a second `--no-publish` edit reads the *published*
layer (without the first edit) and PATCHes over it — silently discarding the first.
For several edits, just run them sequentially with the default `--publish` (each
publishes before the next reads); reserve `--no-publish` for a single staged edit
you publish yourself.

**JSON contract — same envelope as all customization verbs:**

```json
{ "ok": true,
  "data": {"sitemapid": "…", "action": "add-area", "area_id": "cwx_sales",
           "title": "Sales", "updated": true, "published": true},
  "meta": {} }
```

`data` carries the edit's identifying fields (`action`, plus `area_id` /
`group_id` / `sub_id` / `node_id` per verb). `meta.warnings` carries the cascade
advisory (Area/Group with descendants removed) and any solution advisory.
`--dry-run` returns `{_dry_run: true, would_edit: true, sitemapxml: "<…>"}` — reads
run for real (parent validation, entity existence), no PATCH is issued.

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
crm --json ribbon set-label account --solution cwx_crmworx --button-id <CustomAction_Id> ...
crm --json ribbon remove account --solution cwx_crmworx ...
crm --json ribbon hide-button account --solution cwx_crmworx --target-id <OOB_Id>
crm --json ribbon set-rules account --solution cwx_crmworx \
    --command-id account.form.MyBtn.Command \
    --enable-rule Mscrm.SelectionCountExactlyOne
crm --json ribbon add-custom-rule account --solution cwx_crmworx \
    --command-id account.form.MyBtn.Command \
    --webresource cwx_/scripts/ribbon.js --function ns.canRun
```

**`ribbon export` — give exactly one target.** An `ENTITY` exports that one
table's ribbon; `--application` exports the app-wide ribbon (the commands not
bound to any table). They are mutually exclusive — omitting both, or giving both,
errors. The app-wide path returns its zipped XML under `CompressedApplicationRibbonXml`
(not the entity path's `CompressedEntityXml`) — relevant only if you decode the
raw `--dry-run` response yourself.

This is why a cloned entity's ribbon does not come across (see the clone caveats in
`reference/metadata.md`) — there is no API write path to copy it.

**Ribbon writes are slow and synchronous.** Because every write rides the solution-zip
pipeline, `add-button` / `set-label` / `remove` / `hide-button` / `set-rules` /
`add-custom-rule` run a **full solution import per call** — 60–120s with no progress
ticks. The command has not hung; **do not retry** a slow call (a second, parallel
attempt races the first import). Confirm the outcome afterward with `ribbon list`.

**Platform rule allow-list — the server silently ignores unknown `Mscrm.*` ids.**
`set-rules` validates each `Mscrm.*` id against a curated allow-list and rejects
unrecognised platform ids before touching the solution, because the server would
otherwise accept the import and silently discard the unrecognised rule with no error.
Custom (non-`Mscrm.`) ids pass through — they reference rules defined in the same
solution. Allowed platform enable rules: `Mscrm.SelectionCountExactlyOne`,
`Mscrm.ShowOnGrid`, `Mscrm.ShowOnQuickAction`, `Mscrm.ShowOnGridAndQuickAction`.
Allowed platform display rules: `Mscrm.HideOnModern`, `Mscrm.ShowOnlyOnModern`.

**OOB command warning.** Both `set-rules` and `add-custom-rule` emit a
`meta.warnings` entry when `--command-id` is an out-of-the-box (`Mscrm.*`) command.
This is a warning, not a block — the write proceeds — but editing OOB commands is
unsupported ground and can break silently on a platform upgrade.

**`add-custom-rule` rule id.** The generated rule id (`data.rule_id`) follows the
pattern `{command_id}.{slug(function)}.EnableRule`. The rule is both defined in
`RuleDefinitions` and referenced on the command in the same write. To use the same
rule on other commands, pass the returned `rule_id` to `ribbon set-rules
--enable-rule`.

**`set-label` — `$LocLabels` directive ids are case-sensitive.** When `--lcid` is
given, the button attribute is set to a `$LocLabels:<id>` directive and the actual
text lands in a `<Title languagecode=LCID>` row. The directive id is derived
automatically (`{Button-Id}.{Attr}`) — if you hand-edit the RibbonDiffXml and
misspell the id's casing, the label silently falls back to the raw directive string
in the UI. `--lcid` is validated against the org's provisioned languages and errors
if not provisioned. Re-running for a second LCID adds a sibling `<Title>` (does not
overwrite).

**`hide-button` — validate the target-id first.** `--target-id` is the OOB control Id
from `crm ribbon export ENTITY`. The command validates it against the live composed
ribbon before touching the solution, so a typo errors immediately rather than silently
completing a full import with no effect. If validation fails, re-export and find the
exact `Id=` attribute on the `<Button>` or `<FlyoutAnchor>` element.

**Two hide methods — choose by reversibility.**
`--method display-rule` (default) overrides the button's command with two always-false
platform DisplayRules. **Reversible** — delete the override to restore the button.
`--method hide-action` writes a `HideCustomAction`. **One-way trapdoor** — the button
cannot be restored without shipping a new solution version; the command therefore prompts
for confirmation, and `--yes` skips that prompt (required to run non-interactively, e.g.
under `--json`). Neither method touches the button's `classid`, `Command`, or
`TemplateAlias`. Both warn that hiding OOB commands is unsupported ground.

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

### Toggle field presentation properties — `set-field-props`

```bash
crm --json form set-field-props cwx_ticket cwx_priority \
    --disabled --hidden --locked --no-show-label --publish
# → data: {updated: true, published: true, disabled: true, visible: false, locked: true, show_label: false}
```

Toggles presentation attributes of an existing field in-place (no GUID/classid
surface). At least one flag is required; omitted flags are left untouched — and only
the flags you pass echo back in the result (keyed by flag name, e.g. `locked`, not the
underlying `locklevel`). Errors if the field is not on the form — use `add-field` first.

**`--required` routes to metadata, not the form.** Required-level is an attribute
metadata property, not a form property. Passing `--required LEVEL` here errors with a
clear redirect to `crm metadata update-attribute ENTITY ATTRIBUTE --required LEVEL`
rather than silently no-op'ing at the form layer.

**Cell vs control — where each flag lands.** `disabled` is a `<control>` attribute;
`locklevel`, `showlabel`, and `visible` are `<cell>` attributes. The FormXml schema
rejects `visible` on a `<control>` — the CLI applies each flag to the correct element.

**`--dry-run`** returns `{_dry_run: true, would_update: true, …}` (plus the echoed
flags) with no PATCH.

### Wire JS event handlers — `add-library`, `add-handler`, `remove-handler`, `list-handlers`

**Web resource must already exist.** The editor never creates web resources. Register
with `webresource create` first, then wire.

```bash
# 1. Register the library only (idempotent — safe to repeat)
crm --json form add-library cwx_ticket --library cwx_/scripts/ticket.js

# 2. Wire a handler (registers the library too — deduped)
crm --json form add-handler cwx_ticket \
    --event onload --library cwx_/scripts/ticket.js --function App.onLoad

# onchange needs --field naming a field that is already on the form
crm --json form add-handler cwx_ticket \
    --event onchange --field cwx_priority \
    --library cwx_/scripts/ticket.js --function App.onPriorityChange

# 3. Inspect
crm --json form list-handlers cwx_ticket
# → data: bare array [{event, field, function, library, enabled, pass_context, handler_unique_id}];
#   meta: {formid, form}. Only <Handlers> (customizer-owned) — never <InternalHandlers>.

# 4. Remove (event + function; add --field for onchange)
crm --json form remove-handler cwx_ticket \
    --event onload --function App.onLoad
```

**`--field` is required for `onchange`, invalid for `onload`/`onsave`.** The command
also validates that `--field` is on the form before wiring. Duplicate handlers (same
event + function) are refused.

**`--dry-run`:** reads run for real; no PATCH. add-library → `would_add_library`,
add-handler → `would_add_handler`, remove-handler → `would_remove_handler`.

**Handlers vs InternalHandlers.** Every `<event>` element holds two sibling blocks:
`<Handlers>` (customizer-owned, what the CLI writes) and `<InternalHandlers>`
(platform-owned, never touched). `list-handlers` reports only `<Handlers>`. Do not
hand-splice entries into `<InternalHandlers>`.

**Publish gotcha — same as field editors.** `GET /systemforms` returns the *published*
snapshot. Chain `--no-publish` edits on the same form and only the last write survives
(each reads the published state). Publish each step, or batch the no-publish writes
and publish once at the end with `crm solution publish`.

### Edit the tab/section skeleton — `form {add,remove,rename,move}-{tab,section}`

Eight verbs edit the form's tab/section structure (the same PATCH + publish-gotcha
pipeline as the field verbs; `--dry-run` returns `would_add` / `would_remove` /
`would_rename` / `would_move`):

```bash
crm --json form add-tab cwx_ticket cwx_details --label "Details"     # tab + starter section
crm --json form add-section cwx_ticket cwx_status --tab cwx_details  # section into a tab
crm --json form move-tab cwx_ticket cwx_details --after "General"    # reorder
crm --json form remove-tab cwx_ticket cwx_details --force            # --force orphans bound fields
```

Gotchas the flags don't tell you:

- A **new tab always carries a non-empty starter section** — an empty tab is
  XSD-valid but renders broken, so the verbs never produce one. `add-section` is the
  way to create a section to target before `add-field` on a sectionless tab.
- `rename-{tab,section}` changes the **display label only**; the logical `name`
  (what form scripts and `--tab`/`--section`/`--after` match on) is left intact.
- `remove-{tab,section}` **refuses an orphaning remove** (a tab/section still holding
  bound fields) and `remove-tab` refuses the **only** tab. Pass `--force` to remove
  anyway; the orphaned field names come back in the response under `orphaned`.
- Sections default to the **first tab** when `--tab` is omitted.

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

### Chart editors — `update`, `set-fetch`, `add-series`, `remove-series`, `set-groupby`

Five in-place editor verbs mutate a chart without recreating it. All honor
`--user`, `--solution`, `--require-solution`, and `--publish` / `--no-publish`.

```bash
# update: replace XML, name, description, or ChartType on every <Series>
crm --json chart update <id> --data-description d.xml --presentation-description p.xml
crm --json chart update <id> --name "New Name" --type Bar

# set-fetch: swap the inner <fetch> element, keeping the categorycollection
crm --json chart set-fetch <id> --fetch new_query.xml

# add-series / remove-series: add or drop one aggregate series
crm --json chart add-series <id> --column estimatedvalue --aggregate sum --alias total
crm --json chart remove-series <id> --alias total

# set-groupby: change the grouping (category) column
crm --json chart set-groupby <id> --column createdon --dategrouping month
```

**Alias-coupling invariant.** A chart's three XML layers are tightly coupled:
each fetch `<attribute alias="X">` must correspond to a `<measurecollection>`
alias `X` in the datadescription and a positionally-coupled `<Series>` in the
presentationdescription. All editor verbs enforce this invariant. On a partial
`update` (only one XML column given), the other column is read live first so
the full pair can be validated before any write.

**Series cap and comparison-chart rule.** A chart is capped at 5 series. A
comparison chart (2 `<categorycollection>` categories) pairs two groupings
against exactly 1 series, so `add-series` / `remove-series` refuse it (use
`update` to replace its XML); violating the cap is rejected before the write.
`set-groupby --dategrouping` is rejected for a non-date column.

**`--fetch` file format.** Pass the bare `<fetch>` element — not a wrapped
datadescription — to `set-fetch`.

**`primaryentitytypecode` is protected.** No editor verb re-homes a chart to a
different table. To move a chart, use `chart get` to export it and `chart create`
on the new entity.

**Publish gating — system vs user.** System charts (`savedqueryvisualization`)
only reflect an edit after `PublishAllXml`; chaining `--no-publish` edits means
each verb reads the *published* snapshot and the last write wins. User charts
(`--user`, `userqueryvisualization`) are never published — edits reflect
immediately regardless of the `--publish` flag.

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

### Splicing tiles — `add-chart`, `add-view`, `add-iframe`, `add-webresource`

All four tile-add verbs PATCH the `formxml` column directly and run `PublishAllXml`
by default.

```bash
crm --json dashboard add-chart <dashboard-id> --view <savedqueryid> --chart <savedqueryvisualizationid>
crm --json dashboard add-view  <dashboard-id> --view <savedqueryid>
crm --json dashboard add-iframe <dashboard-id> --url https://example.com/embed
crm --json dashboard add-webresource <dashboard-id> --webresource cwx_/pages/summary.html
```

**`add-chart` live ref validation.** The chart (`savedqueryvisualization`) must be
org-owned and its primary entity must match the view's entity — the CLI rejects a
mismatch up front. Get chart GUIDs from `crm --json chart list <entity>`.

**`add-iframe` — empty URL is refused.** A blank `--url` silently renders the tile
empty in the UI; the CLI refuses it before writing. Always supply a non-empty URL.

**`add-webresource` — validates existence, warns on non-form-enabled types.** The
CLI resolves the web resource (by GUID or unique name) before writing and emits a
`meta.warnings` advisory if it is not form-enabled — only HTML, image (PNG/JPG/GIF/
ICO/SVG), and Silverlight types render as a tile. CSS/JS/data/XSL/RESX types earn
the warning but the write still proceeds.

**One component per section by default.** Each tile lands in its own new section so
the `rowspan == count(<row>)` layout invariant holds. Pass `--section <name|id>` to
place a tile in an existing **empty** section instead — a section already holding a
component is refused.

**Six-component cap is `--force`-overridable**, never a hard block.

**Control ids are auto-uniqued** — the server rejects duplicate ids at publish time
and the CLI prevents that on the write.

**Publish-layer gotcha.** `dashboard get` returns the *published* FormXml. Any
tile-add without `--publish` will not appear in a subsequent `dashboard get` —
publish first, then verify.

### Removing a tile — `remove-component`

```bash
crm --json dashboard remove-component <dashboard-id> --index 0
crm --json dashboard remove-component <dashboard-id> --cell-id <id>
crm --json dashboard remove-component <dashboard-id> --view <savedqueryid>
crm --json dashboard remove-component <dashboard-id> --chart <chart-id>
crm --json dashboard remove-component <dashboard-id> --url https://example.com/embed
```

**Exactly one selector.** Passing more than one or none is a usage error. A value
selector (`--view`, `--chart`, `--url`) that matches zero components or more than one
is also refused — switch to `--cell-id` or `--index` to resolve the ambiguity.

**`--index` is 0-based** among all component cells in document order. Export the
FormXml first (`dashboard get` → jq `.data.formxml`) to find the right position or
cell id before removing.

**Row-padding is reconciled automatically** after removal — empty `<row>` stubs are
trimmed so the `rowspan == count(<row>)` invariant is maintained.

**No layout options.** `remove-component` has no `--tab` / `--section` / `--rowspan`
/ `--colspan` / `--force` flags — those are add-only.

**JSON contract — same as the other tile verbs:**

```json
{ "ok": true,
  "data": {"action": "remove-component", "cell_id": "…", "control_id": "…",
           "published": true},
  "meta": {} }
```

Under `--dry-run`: `{_dry_run: true, would_remove: true, cell_id: "…", control_id: "…"}`.

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
