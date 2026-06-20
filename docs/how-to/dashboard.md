# How-to: dashboard

Author organization-owned **system dashboards** headlessly — list, get, create,
and delete `systemform` records with `type = 0` without opening the dashboard
designer. See the [CLI reference](../reference/cli.md) for every flag.

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
crm dashboard create --name "Sales Overview" --formxml dashboard.xml
```

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
crm dashboard create --name "X" --formxml d.xml --interactive
# error: Interactive-experience (type-10) dashboards are not programmatically
# creatable over the Web API — author them in the dashboard designer.
```

### Publishing

`dashboard create` runs `PublishAllXml` **by default** (the CLI-wide convention
shared with `chart create`, `form clone`, etc.), so a new dashboard is visible
immediately. Defer the publish with `--no-publish` to batch several operations
before a single publish:

```bash
crm dashboard create --name "Q" --formxml d.xml --no-publish
crm solution publish    # publish when ready
```

### Add the dashboard to a solution

```bash
crm dashboard create --name "Q" --formxml d.xml --solution cwx_crmworx
```

Use `--require-solution` to fail if no solution name resolves (from `--solution`
or the profile default).

### Preview without writing

```bash
crm --dry-run dashboard create --name "Q" --formxml d.xml
```

Returns `{_dry_run: true, would_create: {entity_set, body}}` with the fully
resolved request body; no dashboard is created.

## Delete a dashboard

```bash
crm dashboard delete 1111aaaa-2222-bbbb-3333-cccccccccccc
```

Under `--dry-run`, delete returns
`{_dry_run: true, would_delete: true, formid: <id>}` without issuing the `DELETE`.
To remove a dashboard from a solution (rather than delete it), use
`crm solution remove-component`.
