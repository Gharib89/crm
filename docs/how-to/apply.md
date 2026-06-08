# How-to: apply

`crm apply -f spec.yaml` stands up a whole custom table — publisher, solution,
entity, columns, option sets, relationships, and views — from a single
declarative spec. It orchestrates the existing metadata commands in dependency
order (publisher → solution → entities → option sets → attributes →
relationships → views), creating each with `if_exists=skip` and running
`PublishAllXml` **once** at the end. Re-applying an unchanged spec is a no-op.

See the [CLI reference](../reference/cli.md) for the flags.

## Spec schema

The spec is YAML (JSON is also accepted — it is a YAML subset). Every section is
optional; provide only what you want to create.

```yaml
publisher:
  unique_name: contosopub          # required
  friendly_name: Contoso Publisher
  prefix: contoso                  # required — 2-8 alphanumerics, customizationprefix
  option_value_prefix: 10000    # required — 10000-99999
solution:
  unique_name: ContosoCore         # required; created components land here
  friendly_name: Contoso Core
  version: 1.0.0.0
optionsets:                     # global (org-level) option sets
  - name: contoso_priority         # required, must include the publisher prefix
    display_name: Priority      # required
    options:
      - {value: 100000000, label: Low}
      - {value: 100000001, label: High}
entities:
  - schema_name: contoso_Project   # required, PascalCase with prefix
    display_name: Project        # required
    display_collection_name: Projects
    ownership: UserOwned
    primary_attr: {schema_name: contoso_Name, label: Name}
    attributes:
      - {kind: string,   schema_name: contoso_Code,     display_name: Code, max_length: 100}
      - {kind: picklist, schema_name: contoso_Priority, display_name: Priority, optionset_name: contoso_priority}
      - {kind: lookup,   schema_name: contoso_Owner,    display_name: Owner, target_entity: systemuser}
    relationships:
      - schema_name: contoso_project_task
        referenced_entity: contoso_project   # the "1" side
        referencing_entity: contoso_task     # the "many" side
        lookup_schema: contoso_ProjectId
        lookup_display: Project
    views:
      - {name: Active Projects, columns: [contoso_name, contoso_code]}
```

`attributes[].kind` is any kind `metadata add-attribute` accepts (`string`,
`memo`, `integer`, `bigint`, `decimal`, `double`, `money`, `boolean`,
`datetime`, `picklist`, `multiselect`, `image`, `file`, `lookup`). A `picklist`
needs `optionset_name` (a global set, usually declared under `optionsets`) **or**
inline `options`; a `lookup` needs `target_entity`. View `columns` are entity
**logical** names; use `name:width` (or `{name, width}`) to set a column width
(default 100). Malformed input is rejected up front, before any HTTP call.

## Stand up a table in one shot

```bash
crm --json apply -f project.yaml
```

Output is `{ok, data:{applied, skipped, planned, failed}, meta:{staged}}`. Each
entry is `{kind, name}`; a `failed` entry also carries `error`. A second run
reports everything under `skipped`.

## Preview a greenfield spec

```bash
crm --dry-run --json apply -f project.yaml
```

Dry-run reports everything that would be created under `planned` and makes no
changes. Dependents of a not-yet-created resource (a column on a new table, a
picklist on a new option set, a solution on a new publisher) are reported
`planned` too, instead of erroring.

> A brand-new table's `ObjectTypeCode` is often not readable until the apply's
> final publish, so its **views land as `planned`**. Run `apply` a second time
> after the first publish to create them.

## Stage without publishing

```bash
crm --stage-only --json apply -f project.yaml
```

`--stage-only` creates every component but skips `PublishAllXml`; `meta.staged`
is `true`. Publish later with `crm solution publish`.

## Partial failure

Metadata POSTs are **not transactional**. If a step fails, `apply` aborts the
remaining steps, reports the failure under `failed` (with the error), exits
non-zero, and does **not** publish. Whatever was already created is left
staged-but-unpublished (`meta.staged` is `true`) — fix the spec and re-apply;
the already-created resources are skipped.

## Referenced global option sets

When a spec contains an `optionsets` block and a `--solution` is provided, `apply`
automatically adds each referenced global option set to the solution (via
`AddSolutionComponent`) even if the option set already existed and was skipped
during creation. This ensures option sets created in a previous apply run (or
pre-existing) are properly linked to the solution.

To opt out:

```bash
crm apply -f spec.yaml --solution MySolution --no-include-referenced-optionsets
```

This flag is also exposed as `include_referenced_optionsets` on the Python
`apply_spec` function for programmatic callers.
