# Schema authoring — apply, scaffold, views, stage-then-publish

Stand up tables, columns, option sets, and views — declaratively or imperatively.
Commands: top-level `apply`, `scaffold table`, `view create`, the `metadata create-*`
verbs, and the publish flow. Flags/choices: `crm describe apply`, `crm <group> --help`.

## Declarative apply — `apply -f spec.yaml`

Stand up a whole table from one YAML/JSON spec instead of many imperative commands.
`apply` runs the metadata cores in dependency order (publisher → solution → entities →
option sets → attributes → relationships → views), each with `if_exists=skip`, and
**publishes once at the end** — re-applying an unchanged spec is a no-op.

```bash
crm --json apply -f project.yaml              # create/skip, publish once
crm --dry-run --json apply -f project.yaml    # plan: dependents reported "planned"
crm --stage-only --json apply -f project.yaml # create without publishing
```

Emits `{ok, data:{applied, skipped, planned, failed}, meta:{staged}}`; each entry is
`{kind, name}` (a failed entry adds `error`). **Metadata POSTs are non-transactional,
so a failure aborts-and-reports and leaves staged-but-unpublished residue.** A new
table's views may report `planned` until the first publish assigns its ObjectTypeCode
— **re-apply to land them.**

```yaml
publisher: {unique_name: contosopub, prefix: contoso, option_value_prefix: 10000}
solution:  {unique_name: ContosoCore}
optionsets:
  - {name: contoso_priority, display_name: Priority, options: [{value: 100000000, label: Low}]}
entities:
  - schema_name: contoso_Project
    display_name: Project
    primary_attr: {schema_name: contoso_Name, label: Name}
    attributes:
      - {kind: string,   schema_name: contoso_Code,     display_name: Code, max_length: 100}
      - {kind: picklist, schema_name: contoso_Priority, display_name: Priority, optionset_name: contoso_priority}
      - {kind: lookup,   schema_name: contoso_Owner,    display_name: Owner, target_entity: systemuser}
    views:
      - {name: Active Projects, columns: [contoso_name, contoso_code]}
```

## Scaffold a table — `scaffold table`

Quick one-liner to create an entity + N columns in a single publish, through the same
`apply` engine. Each resource is `if_exists=skip` — re-running is a no-op.

```bash
crm --json scaffold table "Project" \
  --column "Name:string:max_length=200,required=ApplicationRequired" \
  --column "Due Date:datetime" \
  --column "Owner:lookup:target_entity=systemuser" \
  --column "Priority:picklist:optionset_name=new_priority"

crm --dry-run --json scaffold table "Project" --column "Name:string"   # plan only
crm --stage-only --json scaffold table "Project" --column "Name:string" # no publish
```

Emits the same `{applied, skipped, planned, failed}` envelope as `apply`.

**Column shorthand:** `DISPLAY:KIND[:key=value,...]`.

### Dry-run reference-check

Under `--dry-run`, the name-taking writes — `scaffold table`, `metadata
create-one-to-many`, and `metadata add-attribute` — resolve the server objects
they would point at (a lookup's target entity, a picklist's global option set,
a relationship's referenced/referencing entities) and report each under
`data.references[] = {kind, value, _exists}`. A reference that does not resolve
keeps the preview non-failing (`ok: true`) and adds a `meta.warnings` advisory
naming it — so a dangling target catches before the real write 400s, even when
the table itself is only `planned`. (`apply -f` does not yet probe references.)

- `string`/`memo` take an optional `max_length` (defaults 100/2000); `max_length` on
  any other kind is an error.
- `lookup` requires `target_entity=<logical_name>`.
- `picklist`/`multiselect` require `optionset_name=<name>` (an **existing global**
  option set — inline options are not supported here; use `apply` for those).

Column schema names are derived `<publisher_prefix>_<PascalCase(DISPLAY)>` from the
profile's `publisher_prefix` (**required — a missing prefix is exit 2**).
`--schema-name` overrides the entity schema only, not column names.

**Limitations:** no views, no inline picklist options, single entity only — use
`apply -f spec.yaml` for those.

## Views — `view create` (savedquery)

```bash
crm --json view create cwx_ticket --name "Active Tickets" --otc 10127 \
    --column "cwx_name:220" --column "cwx_priority:120" \
    --filter-active --if-exists skip
```

The LayoutXml `object` attribute is the entity **ObjectTypeCode (OTC)** — get it from
`metadata entity <name>` (see `reference/metadata.md`). `--column` is repeatable
`'logical[:width]'` with order preserved.

`--order` takes an optional `asc`/`desc` suffix (same `$orderby` idiom as
`query odata --orderby`): `--order createdon` is ascending, `--order 'createdon desc'`
sorts newest-first by writing `descending="true"` into the FetchXml at create time —
no follow-up savedquery PATCH. Bad direction token → usage error (exit 2).

### Edit an existing view

`view create` only makes new views. To change an existing one, PATCH its
`savedquery` row — locate the GUID by name, update `fetchxml`/`layoutxml`, then
publish the owning entity:

```bash
crm --json query odata savedqueries --filter "name eq 'Active Accounts'" \
    --select name,returnedtypecode,savedqueryid
crm entity update savedqueries <savedqueryid> --data-file view.json   # {"fetchxml":"…","layoutxml":"…"}
crm solution publish --xml \
    '<importexportxml><entities><entity>account</entity></entities></importexportxml>'
```

Use `--data-file`, **not** inline `--data` — both blobs are XML full of double
quotes (`version="1.0"`, `name="resultset"`), which must be JSON-escaped; a file
keeps that escaping sane and copy-paste-safe.

`returnedtypecode` is the entity **logical name** — a string like `account`, **not**
an int — so identify the target view by `name` or by `returnedtypecode eq '<logical>'`.

## Stage many changes, then publish once

By default each create/update metadata command auto-publishes. The global
`--stage-only` flag (or `CRM_STAGE_ONLY=1`) suppresses publishing across a batch of
changes — then run `publish-all` once at the end. `--stage-only` forces every
create/update command to `--no-publish`; in `--json` mode the envelope `meta` records
`staged: true`. Combining `--stage-only` with an explicit `--publish` is rejected.

```bash
crm --stage-only metadata add-attribute new_widget \
    --kind string --schema-name new_Label --display Label --max-length 100
crm --stage-only metadata create-optionset --name new_priority --display Priority \
    --option 1:Low --option 2:High
# ... more staged changes ...
crm solution publish-all   # single publish for all staged customizations
```

Publish selectively instead of all-at-once:

```bash
crm solution publish --xml \
    '<importexportxml><entities><entity>account</entity></entities></importexportxml>'
```

To confirm a staged change actually landed after publish, poll with `--expect` (see
`reference/metadata.md`).
