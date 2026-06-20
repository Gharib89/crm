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

## Relationship to `metadata clone-entity --with-charts`

`crm chart` is the standalone surface for the chart logic that
`crm metadata clone-entity --with-charts` uses when duplicating a whole entity.
Use `chart get` + `chart create` to move or version a single chart without
cloning the table itself.
