# How-to: chart

Author system and user charts headlessly — list, get, create, and delete
`savedqueryvisualization` (system) and `userqueryvisualization` (user) records
without opening the chart designer. See the
[CLI reference](../reference/cli.md) for every flag.

A chart binds to its host table via `primaryentitytypecode` (the logical-name
string) and carries two XML columns: `datadescription` (an aggregate FetchXML)
and `presentationdescription` (the rendering/series XML). Authoring a chart from
source control means committing those two XML files and recreating the chart
with `chart create`.

## List a table's charts

```bash
crm chart list contact                 # system charts (the default)
crm chart list contact --user          # user-owned charts
```

Output columns: `name`, the id (`savedqueryvisualizationid`, or
`userqueryvisualizationid` with `--user`), and `isdefault` (system charts only).
`list` returns only these list-oriented fields — to read a chart's
`datadescription` / `presentationdescription` XML, use `chart get <id>`.

## Get a single chart

```bash
crm chart get 1111aaaa-2222-bbbb-3333-cccccccccccc
crm chart get 1111aaaa-2222-bbbb-3333-cccccccccccc --user
```

`get` returns the chart's XML in the `--json` envelope — pipe it to files to
capture a chart into source control:

```bash
crm --json chart get <id> | jq -r '.data.datadescription' > chart.data.xml
crm --json chart get <id> | jq -r '.data.presentationdescription' > chart.pres.xml
```

## Create a chart

A system chart is the default; `--user` creates a user-owned chart. There are
two mutually exclusive authoring modes.

### XML mode — from datadescription + presentationdescription files

```bash
crm chart create contact \
    --name "Contacts by Method" \
    --data-description chart.data.xml \
    --presentation-description chart.pres.xml
```

Both files are required in XML mode. The server validates the XML (for example,
the number of chart areas in the presentation XML must match the number of
categories in the data XML), so a malformed pair is rejected with a `400`.

### Web-resource mode — script-based visualization

```bash
crm chart create contact --name "Custom Viz" --web-resource new_chartscript
```

`--web-resource` takes a web resource name or GUID and is mutually exclusive
with `--data-description` / `--presentation-description`. A name is resolved to
its `webresourceid` automatically.

### Create a user chart

```bash
crm chart create contact --name "My View" \
    --data-description chart.data.xml \
    --presentation-description chart.pres.xml \
    --user
```

### Publishing

`chart create` runs `PublishAllXml` **by default** (the CLI-wide convention
shared with `form clone`, `metadata create-entity`, etc.), so a new chart is
visible immediately. Defer the publish with `--no-publish` to batch several
operations before a single publish:

```bash
crm chart create contact --name "Q" \
    --data-description d.xml --presentation-description p.xml --no-publish
crm solution publish    # publish when ready
```

### Add the chart to a solution

```bash
crm chart create contact --name "Q" \
    --data-description d.xml --presentation-description p.xml \
    --solution cwx_crmworx
```

Use `--require-solution` to fail if no solution name resolves (from `--solution`
or the profile default).

### Preview without writing

```bash
crm --dry-run chart create contact --name "Q" \
    --data-description d.xml --presentation-description p.xml
```

Returns `{_dry_run: true, would_create: {entity_set, body}}` with the fully
resolved request body (a `--web-resource` name is resolved live first); no chart
is created.

## Delete a chart

```bash
crm chart delete 1111aaaa-2222-bbbb-3333-cccccccccccc
crm chart delete 1111aaaa-2222-bbbb-3333-cccccccccccc --user
```

Under `--dry-run`, delete returns
`{_dry_run: true, would_delete: true, savedqueryvisualizationid: <id>}` (or
`userqueryvisualizationid` with `--user`) without issuing the `DELETE`. To remove
a chart from a solution (rather than delete it), use
`crm solution remove-component`.

## Update a chart's XML, name, or series type

`chart update` replaces one or both XML columns, the display name, the
description, or the chart type on every series — any combination, in one call:

```bash
crm chart update <id> --data-description new.data.xml
crm chart update <id> --presentation-description new.pres.xml
crm chart update <id> --name "Contacts by Region" --description "Q3 rollout"
crm chart update <id> --type Bar
crm chart update <id> --data-description d.xml --presentation-description p.xml
```

`--type` sets `ChartType` on **every** `<Series>` element in the
presentationdescription (e.g. `Column`, `Bar`, `Line`, `Pie`).

### Partial-XML update and the alias-coupling invariant

When only one of `--data-description` / `--presentation-description` is
supplied, the command reads the other column live from the server and validates
the cross-container alias-coupling pair before PATCHing. This means:

- Every `<attribute alias="…">` in the fetch must match a
  `<measurecollection>` alias in the datadescription and a positionally-coupled
  `<Series>` in the presentationdescription.
- A mismatched pair is rejected before any write is issued.

The chart's host entity (`primaryentitytypecode`) is never changed — re-homing
a chart to a different table is not supported.

### Publishing and solution

`update` follows the same `--publish` / `--no-publish` / `--solution` /
`--require-solution` contract as `create` (see above). For system charts the
change is only visible in the UI after `PublishAllXml` runs; user charts
(`--user`) are never published and take effect immediately.

```bash
crm chart update <id> --type Line --solution cwx_crmworx
crm chart update <id> --name "New Name" --no-publish
crm solution publish    # publish when ready
```

## Replace the inner fetch query

`chart set-fetch` replaces the `<fetch>` element inside the datadescription
while leaving the `<categorycollection>` (grouping categories) intact:

```bash
crm chart set-fetch <id> --fetch new_query.xml
crm chart set-fetch <id> --fetch new_query.xml --user
crm chart set-fetch <id> --fetch new_query.xml --solution cwx_crmworx --no-publish
```

Use this when you need to change the query (entity, filters, linked entities)
without rebuilding the full datadescription. The `--fetch` file should contain
only the `<fetch>` element itself, not a full wrapped datadescription.

The alias-coupling invariant is validated after the splice: the replacement
`<fetch>` must still carry `<attribute>` elements whose aliases match the
existing `<measurecollection>` aliases.

## Add an aggregate series

`chart add-series` adds one new aggregate series — a fetch attribute, a
measurecollection entry, and a presentation `<Series>` — in one call:

```bash
crm chart add-series <id> --column estimatedvalue --aggregate sum --alias total_value
crm chart add-series <id> --column opportunityid --aggregate count --alias opp_count
```

A chart is capped at **5 series**. Per-series edits are not supported on a
**comparison chart** (one with 2 `<categorycollection>` categories — it pairs
two groupings against a single series); `add-series` / `remove-series` refuse it
with a hint to use `chart update` instead.

The `--alias` must be unique within the chart; the `--column` must be a logical
name that exists on the chart's host entity (validated against live metadata).

A series is modeled as one `<measurecollection>` per series — the server couples
the inner `<Series>` count to a category's measurecollection count, not to its
individual `<measure>` count. Keep that 1:1 mapping in mind when inspecting the
raw XML.

## Remove a series

`chart remove-series` removes the series identified by its alias — the fetch
attribute, its measurecollection entry, and the positionally-coupled
presentation `<Series>`:

```bash
crm chart remove-series <id> --alias total_value
crm chart remove-series <id> --alias opp_count --user
```

Removing the **last** series is refused — a chart must have at least one — as is
removing a series from a comparison chart (see `add-series` above).

## Change the grouping column

`chart set-groupby` replaces the grouping (category) column in the fetch's
`<entity>` element and in the datadescription's `<categorycollection>`:

```bash
crm chart set-groupby <id> --column createdon --dategrouping month
crm chart set-groupby <id> --column ownerid
```

`--dategrouping` is only meaningful for date/datetime columns. It is rejected
for non-date columns.

The `--column` is validated against live entity metadata.

## Publish gating — system vs user charts

This applies to all editor verbs (`update`, `set-fetch`, `add-series`,
`remove-series`, `set-groupby`):

- **System charts** (`savedqueryvisualization`, the default) run
  `PublishAllXml` by default. The change is only reflected in the UI after
  publish. Use `--no-publish` to stage a batch of edits, then publish once with
  `crm solution publish`.
- **User charts** (`userqueryvisualization`, `--user`) are **never published**
  — edits reflect immediately and `--publish` / `--no-publish` is accepted but
  has no effect.

!!! warning "Don't chain --no-publish edits"
    Each editor verb reads the **published** snapshot before writing. A second
    `--no-publish` edit reads the chart without the first edit's pending change
    and overwrites it. To make several edits safely: either keep the default
    (publish each step), or publish between edits with `crm solution publish`.

## Relationship to `metadata clone-entity --with-charts`

`crm chart` is the standalone surface for the chart logic that
`crm metadata clone-entity --with-charts` uses when duplicating a whole entity.
Use `chart get` + `chart create` to move or version a single chart without
cloning the table itself.
