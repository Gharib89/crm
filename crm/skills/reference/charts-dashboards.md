# Charts & dashboards — `chart`, `dashboard`

Author charts and organization dashboards headlessly instead of using the
designers. Groups: `chart`, `dashboard`. Flags/choices: `crm <group> --help`.
Mutating verbs are solution-scoped and stage by default (see the SKILL.md agent
contract); `delete` verbs take no `--solution`.

## Charts — `chart` (savedqueryvisualization / userqueryvisualization)

System charts (org-wide, `savedqueryvisualization`) are the default; `--user-owned`
targets user-owned charts (`userqueryvisualization`), which have no `isdefault`
flag and a `userqueryvisualizationid` id field.

```bash
crm --json chart list contact                          # system charts (default)
crm --json chart list contact --user-owned                   # user charts
crm --json chart get <id>                              # single chart, with its XML
crm --json chart delete <id> --yes                     # delete
crm --json chart delete <id> --user-owned --yes              # delete a user chart
```

A chart carries two XML columns: `datadescription` (aggregate FetchXML, references
the host table) and `presentationdescription` (series/areas rendering XML). To
version a chart, capture both from `chart get` and recreate with `chart create`:

```bash
crm --json chart get <id> | jq -r '.data.datadescription' > c.data.xml
crm --json chart get <id> | jq -r '.data.presentationdescription' > c.pres.xml
crm --json chart create contact --name "By Method" \
    --data-description c.data.xml --presentation-description c.pres.xml --solution ContosoCore
```

**Two mutually exclusive create modes.** XML mode needs **both**
`--data-description` and `--presentation-description`; web-resource mode is
`--web-resource <name|GUID>` (resolved to its `webresourceid`). Passing both modes,
or only one XML file, is a usage error.

**Server validates the XML.** The presentation XML's chart-area count must match the
data XML's category count, etc. — a malformed pair fails with a `400`
(`The number of chart areas must be equal to the number of categories.`). When in
doubt, start from a known-good chart captured via `chart get`.

**Dry-run shapes.** `create` returns `{_dry_run, would_create: {entity_set, body}}`
with the resolved body (a `--web-resource` name is resolved live first); `delete`
returns `{_dry_run, would_delete: true, <id>}`. To take a chart *out* of a solution
without deleting it, use `solution remove-component`.

### Chart editors — `update`, `set-fetch`, `add-series`, `remove-series`, `set-groupby`

Five in-place editor verbs mutate a chart without recreating it. All are
solution-scoped and honor `--user-owned` and `--publish` / `--no-publish`.

```bash
# update: replace XML, name, description, or ChartType on every <Series>
crm --json chart update <id> --data-description d.xml --presentation-description p.xml --solution ContosoCore
crm --json chart update <id> --name "New Name" --type Bar --solution ContosoCore

# set-fetch: swap the inner <fetch> element, keeping the categorycollection
crm --json chart set-fetch <id> --fetch new_query.xml --solution ContosoCore

# add-series / remove-series: add or drop one aggregate series
crm --json chart add-series <id> --column estimatedvalue --aggregate sum --alias total --solution ContosoCore
crm --json chart remove-series <id> --alias total --solution ContosoCore

# set-groupby: change the grouping (category) column
crm --json chart set-groupby <id> --column createdon --dategrouping month --solution ContosoCore
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

**User charts are never published.** System charts follow the staged-writes rule
(SKILL.md); user charts (`--user-owned`, `userqueryvisualization`) reflect edits
immediately regardless of the `--publish` flag.

## Dashboards — `dashboard` (systemform type=0)

A dashboard is a `systemform` with `type = 0`; the verbs scope every read to that
type, so other form types never appear.

```bash
crm --json dashboard list                              # org dashboards (no formxml)
crm --json dashboard get <id>                          # single dashboard, with formxml
crm --json dashboard create --name "Sales" --formxml dash.xml --solution ContosoCore
crm --json dashboard delete <id> --yes
```

**The CLI does not author FormXml** — it posts the file verbatim. To version a
dashboard, capture its layout from `dashboard get` and recreate it:

```bash
crm --json dashboard get <id> | jq -r '.data.formxml' > dash.xml
crm --json dashboard create --name "Sales" --formxml dash.xml --solution ContosoCore
```

**Interactive (type-10) dashboards are not API-creatable.** Passing `--interactive`
fails fast with a clear error rather than silently creating a standard dashboard —
author interactive-experience dashboards in the designer.

**Dry-run shapes.** `create` returns `{_dry_run, would_create: {entity_set, body}}`
and `delete` returns `{_dry_run, would_delete: true, formid}` — neither issues the
write.

### Splicing tiles — `add-chart`, `add-view`, `add-iframe`, `add-webresource`

All four tile-add verbs PATCH the `formxml` column directly.

```bash
crm --json dashboard add-chart <dashboard-id> --view <savedqueryid> --chart <savedqueryvisualizationid> --solution ContosoCore
crm --json dashboard add-view  <dashboard-id> --view <savedqueryid> --solution ContosoCore
crm --json dashboard add-iframe <dashboard-id> --url https://example.com/embed --solution ContosoCore
crm --json dashboard add-webresource <dashboard-id> --webresource contoso_/pages/summary.html --solution ContosoCore
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

**Publish first, then verify.** `dashboard get` returns the *published* FormXml
(staged writes, SKILL.md) — a tile-add without `--publish` will not appear in a
subsequent `dashboard get`.

### Removing a tile — `remove-component`

```bash
crm --json dashboard remove-component <dashboard-id> --index 0 --solution ContosoCore
crm --json dashboard remove-component <dashboard-id> --cell-id <id> --solution ContosoCore
crm --json dashboard remove-component <dashboard-id> --view <savedqueryid> --solution ContosoCore
crm --json dashboard remove-component <dashboard-id> --chart <chart-id> --solution ContosoCore
crm --json dashboard remove-component <dashboard-id> --url https://example.com/embed --solution ContosoCore
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

**JSON contract — same as the other tile verbs:** `data` always carries
`updated: true` on a real write; `published: true` is added only with `--publish`.

```json
{ "ok": true,
  "data": {"action": "remove-component", "cell_id": "…", "control_id": "…",
           "updated": true},
  "meta": {} }
```

Under `--dry-run`: `{_dry_run: true, would_remove: true, cell_id: "…", control_id: "…"}`.
