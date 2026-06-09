# How-to: scaffold

`crm scaffold table` is the quick one-liner path to stand up a new custom table
with columns. It builds a one-entity in-memory spec from the given display name
and column shorthands, then runs it through the same `crm.core.apply.apply_spec`
engine that `apply -f spec.yaml` uses. One `PublishAllXml` fires at the end
(or is suppressed by `--stage-only`). Re-running the same command is a no-op
because every resource is created with `if_exists=skip`.

See the [CLI reference](../reference/cli.md) for the full flag list.

## When to use scaffold vs apply

| Need | Use |
|------|-----|
| A new entity with some typed columns, fast | `scaffold table` |
| A publisher, solution, or global option sets | `apply -f spec.yaml` |
| Inline picklist options | `apply -f spec.yaml` |
| Relationships or views | `apply -f spec.yaml` |
| Multiple entities in one shot | `apply -f spec.yaml` |

`scaffold table` does **not** create views and does **not** support inline
picklist options in the shorthand. For those, declare a full YAML spec and use
[`crm apply`](apply.md).

## Column shorthand grammar

Each `--column` value is a colon-separated shorthand:

```
DISPLAY:KIND[:key=value,key=value,...]
```

`KIND` must be one of:

| KIND | Notes |
|------|-------|
| `string` | Default `max_length=100`; override with `max_length=N` |
| `memo` | Default `max_length=2000`; override with `max_length=N` |
| `integer` | — |
| `bigint` | — |
| `decimal` | — |
| `double` | — |
| `money` | — |
| `boolean` | — |
| `datetime` | — |
| `picklist` | Requires `optionset_name=<name>` (existing global option set) |
| `multiselect` | Requires `optionset_name=<name>` (existing global option set) |
| `lookup` | Requires `target_entity=<logical_name>` |
| `image` | — |
| `file` | — |

Optional opts (any KIND):

| Opt | Values |
|-----|--------|
| `required` | `None` \| `Recommended` \| `ApplicationRequired` |
| `description` | Free text — **no commas** (opts are comma-separated) |

Opts are split on commas, so a `description` value cannot contain `,`. For
descriptions with commas, create the column with `crm metadata add-attribute`
or a declarative `apply` spec instead.

`max_length` is only valid for `string` and `memo`; using it on any other kind
is a validation error (exit 1 — the failure envelope, like any malformed
`--column`). Pass `--column` once per column — the flag is repeatable.

Schema names for columns are derived as `<publisher_prefix>_<PascalCase(DISPLAY)>`.
A profile without a `publisher_prefix` causes exit 2 before any network call.
`--schema-name` overrides the **entity** schema name only, not column names.

## Examples

### Create a table with typed columns

```bash
crm --json scaffold table "Project" \
  --column "Name:string:max_length=200,required=ApplicationRequired" \
  --column "Due Date:datetime" \
  --column "Budget:money" \
  --column "Owner:lookup:target_entity=systemuser" \
  --column "Priority:picklist:optionset_name=new_priority"
```

Output:

```json
{
  "ok": true,
  "data": {"applied": [...], "skipped": [], "planned": [], "failed": []},
  "meta": {"staged": false}
}
```

Each entry in `applied` / `skipped` / `planned` / `failed` is `{kind, name}`;
a `failed` entry also carries `error`. A second run moves everything to `skipped`.

### Preview without making changes

```bash
crm --dry-run --json scaffold table "Project" \
  --column "Name:string" \
  --column "Due Date:datetime"
```

Dry-run reports the entity and all columns as `planned` and makes no create
calls. On a greenfield org (the table does not yet exist) only the entity
existence GET fires — the columns are reported `planned` off the planned entity
without their own probes. If the table already exists, apply also probes each
column (and resolves any referenced option sets) to classify entries as
`planned` vs `skipped`, so you will see additional GETs. The envelope carries
`meta.dry_run: true`.

### Create without publishing

```bash
crm --stage-only --json scaffold table "Project" \
  --column "Name:string" \
  --column "Due Date:datetime"
```

Creates every component but skips `PublishAllXml`. When at least one component
was created the envelope carries `meta.staged: true` (an all-skipped no-op
re-run stays `false`, since nothing is left unpublished). Publish later with
`crm solution publish-all`.

### Override the entity schema name and plural label

```bash
crm --json scaffold table "Work Item" \
  --schema-name new_WorkItem \
  --display-collection "Work Items" \
  --column "Title:string:max_length=500,required=ApplicationRequired" \
  --column "Assignee:lookup:target_entity=systemuser"
```

### Target a specific solution

```bash
crm --json scaffold table "Project" \
  --solution ContosoCore \
  --column "Name:string"
```

Pass `--require-solution` to fail with exit 2 when no solution resolves instead
of creating the entity without a solution context.

### OrganizationOwned entity

```bash
crm --json scaffold table "Reference Data" \
  --ownership OrganizationOwned \
  --column "Code:string:max_length=50,required=ApplicationRequired" \
  --column "Label:string:max_length=200"
```

## Idempotency

Every resource (entity, then each column in order) is created with
`if_exists=skip`. A second identical run reports everything under `skipped` and
exits 0. This makes `scaffold table` safe to run repeatedly in CI or agent
pipelines without side effects.

## Publisher prefix requirement

Column schema names are derived from the active profile's `publisher_prefix`
(e.g. prefix `new` → column `new_DueDate`). If the active profile has no
`publisher_prefix` set, the command fails immediately with exit 2:

```
Error: scaffold table needs a publisher prefix to derive column schema names;
set publisher_prefix on the active profile (e.g. via crm profile add).
```

## Limitations

- **No views.** `scaffold table` creates only the entity and its columns. To
  create system views, use `apply -f spec.yaml` (which supports a `views:` list
  per entity) or `crm view create`.
- **No inline picklist options.** `picklist` / `multiselect` columns require an
  existing global option set (`optionset_name=<name>`). To define inline options,
  declare an `optionsets:` block in a full apply spec and reference it there.
- **Single entity.** To create multiple entities in one run, use `apply -f spec.yaml`.
- **No publisher or solution creation.** Those must exist (or be created via
  `apply` or `crm solution create-publisher` / `crm solution create`) before
  targeting them with `--solution`.
