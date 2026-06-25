# How-to: apply

`crm apply -f spec.yaml` stands up a whole custom table — publisher, solution,
entity, columns, option sets, relationships, views, web resources, and security
roles — from a single declarative spec. It orchestrates the existing metadata
commands in dependency order (publisher → solution → entities → option sets →
attributes → relationships → views → web resources → security roles) and runs
`PublishAllXml` **once** at the end — but only when a publishable component was
created or updated. Security roles are not publishable customizations, so a
role-only apply does not publish.

`apply` is **convergent**: a component that already exists is reconciled against
the spec rather than blindly skipped. Three outcomes per component:

- **equal** — spec matches live definition → `skipped` (idempotent re-apply).
- **updatable divergence** — an in-place-editable field drifted → updated in place
  (a retrieve-merge-write PUT or option-set action, not HTTP PATCH), counted as
  `updated`. Updatable fields: entity display name / display-collection
  name / description; attribute display name, description, required level, and
  string `max_length` growth (shrinking is out of scope); adding declared options
  to a global option set.
- **immutable/destructive divergence** — the change cannot be made without
  dropping the component (entity ownership change, attribute data-type change)
  → `replace_blocked`: reported, **no write for that component**, run ends
  `ok=false` (exit 1).

Reconciliation also runs under `--dry-run`, read-only: it reads the live org
while the reads-execute rule suppresses every write, so a dry-run reports the
full drift — `planned` (would create), `updated` (would update, each entry
carrying a field-level `diff`), `replace_blocked`, and `pruned` — without issuing
a write.

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
webresources:
  - name: contoso_/scripts/project.js   # required, unique name (must include publisher prefix)
    file: scripts/project.js            # required, path relative to the spec file
    display_name: Project Script        # optional
    # webresourcetype omitted — inferred from .js extension
security_roles:
  - name: Contoso Project Manager       # required, role display name (key)
    # business_unit omitted — defaults to the caller's business unit
    privileges:
      - access: [read, write, create]
        entities: [contoso_project, contoso_task]
        depth: deep
      - access: [read]
        all_entities: true
        depth: basic
      - privilege_names: [prvReadSystemForm]
        depth: global
```

`attributes[].kind` is any kind `metadata add-attribute` accepts (`string`,
`memo`, `integer`, `bigint`, `decimal`, `double`, `money`, `boolean`,
`datetime`, `picklist`, `multiselect`, `image`, `file`, `lookup`). A `picklist`
needs `optionset_name` (a global set, usually declared under `optionsets`) **or**
inline `options`; a `lookup` needs `target_entity`. `max_length` is optional for
`string`/`memo` (defaults to 100 / 2000 when omitted) and rejected on any other kind.
View `columns` are entity
**logical** names; use `name:width` (or `{name, width}`) to set a column width
(default 100). Malformed input is rejected up front, before any HTTP call.

`webresources[].webresourcetype` is an integer (1=HTML, 2=CSS, 3=JS, 4=XML,
5=PNG, 6=JPG, 7=GIF, 8=XAP, 9=XSL, 10=ICO, 11=SVG, 12=RESX). When omitted, the
type is inferred from the file extension. `file` is resolved relative to the spec
file's directory. Web resources are published by the end-of-run `PublishAllXml`
(deferred by `--stage-only`). Convergent: unchanged content → `skipped`; content
or display name drift → `updated`.

`security_roles[].privileges` is a list of grant rows that are merged into the
declared set (highest depth wins per privilege). Each row specifies `depth`
(`basic`/`local`/`deep`/`global`) and either:

- `access` (list of actions: `read`, `write`, `create`, `delete`, `append`,
  `appendto`, `assign`, `share`) with `entities` (list of logical names) **or**
  `all_entities: true`, or
- `privilege_names` (list of privilege names like `prvReadAccount`).

Convergent: skipped when every declared privilege is already present at its
declared depth; on drift, replaced to the declared set (extras dropped).

## Security role privileges: platform baseline and removal-only no-op

Dataverse automatically grants every role a set of **immovable baseline
privileges** (e.g. SharePoint document management, prvReadSharePointData). These
cannot be removed via `ReplacePrivilegesRole`. "Exactly the declared set" means
the declared privileges at their declared depths **plus** those immovable platform
privileges — apply will not report a `replace_blocked` for them.

A privilege **dropped** from the spec is only removed if some other declared
privilege also drifts in the same run (triggering a fresh replace). A
**removal-only change** — where all remaining declared privileges are already
satisfied — is a convergent no-op. To force a remove-only reconciliation, make a
no-op edit to another privilege in the role (e.g. increment and reset a depth),
or use `crm security set-role-privileges` directly.

## Stand up a table in one shot

```bash
crm --json apply -f project.yaml
```

Output is `{ok, data:{applied, updated, skipped, replace_blocked, pruned, planned, failed}, meta:{staged}}`.
Each entry is `{kind, name}`; `failed` and `replace_blocked` entries also carry
`error` / `reason`. A re-apply of an unchanged spec reports all matching
components under `skipped`. A re-apply of a changed spec reports in-place edits
under `updated`; immutable divergences under `replace_blocked` (and exits 1).
`pruned` is reserved for a future opt-in pruning slice and is always empty today.

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
