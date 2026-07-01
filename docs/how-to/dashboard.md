# How-to: dashboard

Author organization-owned **system dashboards** headlessly — list, get, create,
delete, and splice tiles into `systemform` records with `type = 0` without
opening the dashboard designer. See the [CLI reference](../reference/cli.md)
for every flag.

A dashboard's layout lives in its `formxml` column. The CLI does **not** generate
that XML — it posts the file you give it verbatim — so authoring a dashboard from
source control means committing its FormXml and recreating it with
`dashboard create`. `systemform` also backs every other form type (main,
quick-create, card, …); every `dashboard` verb scopes its reads to `type eq 0`,
so the group only ever sees dashboards.

## List dashboards

```bash
crm dashboard list
```

Output columns: `name`, `formid`, and `isdefault`. `list` returns only these
list-oriented fields — to read a dashboard's `formxml`, use `dashboard get <id>`.

## Get a single dashboard

```bash
crm dashboard get 1111aaaa-2222-bbbb-3333-cccccccccccc
```

`get` returns the dashboard's FormXml in the `--json` envelope — capture it into
source control to version a dashboard:

```bash
crm --json dashboard get <id> | jq -r '.data.formxml' > dashboard.xml
```

## Create a dashboard

```bash
crm dashboard create --name "Sales Overview" --formxml dashboard.xml --solution cwx_crmworx
```

`--solution` is required — a component created without an explicit target
solution would otherwise land only in the system Default Solution. Pass
`--solution Default` for a deliberate Default-Solution-only write.

`--formxml` takes the path to a dashboard FormXml file. The created record is an
organization-owned dashboard (`objecttypecode` `none`), not bound to a single
table. The server validates the FormXml, so a malformed layout is rejected with a
`400`. A round-tripped FormXml from `dashboard get` is the most reliable starting
point.

### Interactive dashboards are not creatable

Interactive-experience (type-10) dashboards cannot be created over the Web API.
Passing `--interactive` fails fast with a clear error instead of silently creating
a standard dashboard:

```bash
crm dashboard create --name "X" --formxml d.xml --interactive --solution cwx_crmworx
# error: Interactive-experience (type-10) dashboards are not programmatically
# creatable over the Web API — author them in the dashboard designer.
```

### Publishing

`dashboard create` runs `PublishAllXml` **by default** (the CLI-wide convention
shared with `chart create`, `form clone`, etc.), so a new dashboard is visible
immediately. Defer the publish with `--no-publish` to batch several operations
before a single publish:

```bash
crm dashboard create --name "Q" --formxml d.xml --solution cwx_crmworx --no-publish
crm solution publish    # publish when ready
```

### Preview without writing

```bash
crm --dry-run dashboard create --name "Q" --formxml d.xml --solution cwx_crmworx
```

Returns `{_dry_run: true, would_create: {entity_set, body}}` with the fully
resolved request body; no dashboard is created. `--solution` is still required
under `--dry-run` — it is validated before any backend call.

## Delete a dashboard

```bash
crm dashboard delete 1111aaaa-2222-bbbb-3333-cccccccccccc
```

Under `--dry-run`, delete returns
`{_dry_run: true, would_delete: true, formid: <id>}` without issuing the `DELETE`.
To remove a dashboard from a solution (rather than delete it), use
`crm solution remove-component`.

## Add a chart tile — `dashboard add-chart`

Splices a ChartGrid tile (a chart rendered above its grid) into an existing
dashboard's FormXml without touching the dashboard designer:

```bash
crm dashboard add-chart 1111aaaa-2222-bbbb-3333-cccccccccccc \
    --view  <savedquery-id> \
    --chart <savedqueryvisualization-id> \
    --solution cwx_crmworx
```

`--view` is the `savedqueryid` of the public view whose data the grid shows.
`--chart` is the `savedqueryvisualizationid` of an org-owned chart; its primary
entity must match the view's entity — the CLI validates both references live and
rejects a mismatch before writing. `--solution` is required, as on every
mutating dashboard verb (`add-*`, `remove-component`, `create`) — pass
`--solution Default` for a deliberate Default-Solution-only write.

### Tile placement

By default each tile lands in its own **new section** so that the
`rowspan == count(<row>)` layout invariant holds per component. To place a tile
in an existing section, pass `--section <name|id>` — the section must be
**empty** (have no component yet), since a section holds at most one component
while keeping the invariant; targeting an occupied section is refused:

```bash
crm dashboard add-chart <dashboard-id> --view <v> --chart <c> \
    --tab "Sales" --section "Pipeline" --solution cwx_crmworx
```

`--rowspan` and `--colspan` control the cell size; the section is padded to
match `--rowspan`.

### Six-component cap

Dashboards have a default six-component cap. `add-chart` refuses to exceed it
unless you pass `--force`:

```bash
crm dashboard add-chart <dashboard-id> --view <v> --chart <c> --force --solution cwx_crmworx
```

### Publishing

`add-chart` runs `PublishAllXml` by default. Defer with `--no-publish` to batch
several tile-add calls before a single publish:

```bash
crm dashboard add-chart <id> --view <v> --chart <c> --solution cwx_crmworx --no-publish
crm dashboard add-view  <id> --view <v2>             --solution cwx_crmworx --no-publish
crm solution publish
```

> **Gotcha — `dashboard get` returns the published layer.** Until you publish,
> a `dashboard get` read-back will show the *old* FormXml. Always pass
> `--publish` (or run `crm solution publish`) before verifying the edit.

## Add a view-only grid tile — `dashboard add-view`

Splices a view-only grid tile (no chart) into an existing dashboard:

```bash
crm dashboard add-view 1111aaaa-2222-bbbb-3333-cccccccccccc \
    --view <savedquery-id> --solution cwx_crmworx
```

`--mode list` (the default) renders the grid alone. `--mode all` renders the
grid with the chart-toggle control so the user can switch to a chart in the UI
without a fixed chart selection:

```bash
crm dashboard add-view <dashboard-id> --view <v> --mode all --solution cwx_crmworx
```

`--records-per-page` sets the row count per page in the grid (default 10).

All placement, cap, and publish options work identically to `add-chart`.

## Add an IFRAME tile — `dashboard add-iframe`

Splices an IFRAME tile into an existing dashboard's FormXml:

```bash
crm dashboard add-iframe 1111aaaa-2222-bbbb-3333-cccccccccccc \
    --url https://example.com/embed --solution cwx_crmworx
```

`--url` is **required and must be non-empty.** An IFRAME tile with an empty URL
renders silently blank in the UI — the CLI refuses it before writing.

The `--security`, `--scrolling`, `--border`, and `--pass-parameters` flags map
directly to the FormXml typed-boolean parameters that control cross-frame
scripting restriction, scrollbar visibility, border rendering, and whether the
record's object-type code and id are appended as URL query parameters
respectively.

All placement, cap, and publish options work identically to `add-chart`.

### Preview without writing

```bash
crm --dry-run dashboard add-iframe <id> --url https://example.com/embed --solution cwx_crmworx
```

Returns `{_dry_run: true, would_add: true, url: "..."}` without patching the
dashboard.

## Add a web-resource tile — `dashboard add-webresource`

Splices a web-resource tile into an existing dashboard's FormXml:

```bash
crm dashboard add-webresource 1111aaaa-2222-bbbb-3333-cccccccccccc \
    --webresource cwx_/pages/summary.html --solution cwx_crmworx
```

`--webresource` accepts either a GUID or the web resource's unique name. The CLI
validates the web resource exists before writing; it emits a warning (in
`meta.warnings`) when the resource is not form-enabled — CSS, scripts, data XML,
XSL, and RESX types do not render as dashboard tiles (only HTML, images, and
Silverlight do). The write still proceeds; the warning is advisory.

The tile's `<Url>` is set to `$webresource:<name>` — the platform directive that
resolves the resource's hosted URL on the server side.

All placement, cap, and publish options work identically to `add-chart`.

### Preview without writing

```bash
crm --dry-run dashboard add-webresource <id> --webresource cwx_/pages/summary.html \
    --solution cwx_crmworx
```

Returns `{_dry_run: true, would_add: true, webresource: "..."}` (the web resource
is still resolved live to validate it exists; no PATCH is issued).

## Remove a tile — `dashboard remove-component`

Removes exactly one tile from an existing dashboard's FormXml, selected by
**exactly one** of five selectors:

```bash
crm dashboard remove-component <dashboard-id> --index 0 --solution cwx_crmworx        # first tile (0-based)
crm dashboard remove-component <dashboard-id> --cell-id <id> --solution cwx_crmworx   # by cell id in FormXml
crm dashboard remove-component <dashboard-id> --view <savedqueryid> --solution cwx_crmworx
crm dashboard remove-component <dashboard-id> --chart <savedqueryvisualizationid> --solution cwx_crmworx
crm dashboard remove-component <dashboard-id> --url https://example.com/embed --solution cwx_crmworx
```

Passing more than one selector, or none, is a usage error. Passing a value
selector (`--view`, `--chart`, `--url`) that matches no component or more than one
component is also refused — for an ambiguous multi-match, switch to `--cell-id` or
`--index` to target exactly one tile.

`--index` is 0-based among all component cells in document order. Use
`crm --json dashboard get <id> | jq -r '.data.formxml'` to inspect the FormXml
and find the right index or cell id before removing.

After removal the CLI reconciles the section's empty `<row>` padding so the
`rowspan == count(<row>)` layout invariant is maintained.

`remove-component` does **not** accept the tile layout options (`--tab`,
`--section`, `--rowspan`, `--colspan`, `--force`) — those are add-only.

### Preview without writing

```bash
crm --dry-run dashboard remove-component <id> --index 0 --solution cwx_crmworx
```

Returns `{_dry_run: true, would_remove: true, cell_id: "...", control_id: "..."}`
without patching the dashboard.
