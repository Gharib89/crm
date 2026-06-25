# How-to: apply

`crm apply -f spec.yaml` stands up a whole custom table — publisher, solution,
entity, columns, option sets, relationships, views, web resources, security
roles, and plug-in assemblies with their types, steps, and images — from a
single declarative spec. It orchestrates the existing metadata and plug-in
commands in dependency order (publisher → solution → entities → option sets →
attributes → relationships → views → web resources → security roles → plug-ins)
and runs `PublishAllXml` **once** at the end — but only when a publishable
component was created or updated. Security roles and plug-in components are not
publishable customizations, so an apply that touches only those does not
publish.

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
carrying a field-level `diff`), `replace_blocked`, and `pruned` (with `--prune`)
— without issuing a write.

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
plugins:
  - assembly: Contoso.Plugins           # optional; defaults to the DLL's file stem
    file: bin/Contoso.Plugins.dll       # required; resolved relative to the spec file
    isolation_mode: sandbox             # optional (none|sandbox), default sandbox
    version: 1.0.0.0                    # optional override, default 1.0.0.0
    # culture / public_key_token / description are optional overrides
    types:
      - type_name: Contoso.Plugins.AccountHandler   # required; fully-qualified class (the key)
        friendly_name: Account Handler              # optional
    steps:
      - name: Contoso Account Handler   # required; unique, stable convergent key
        message: Create                 # required; SDK message (e.g. Create, Update)
        plugin_type: Contoso.Plugins.AccountHandler   # required; a registered type (declare it under types to register it)
        entity: account                 # optional; entity scope (omit = message-level)
        stage: postoperation            # optional (prevalidation|preoperation|postoperation), default postoperation
        mode: sync                      # optional (sync|async), default sync
        rank: 1                         # optional, default 1
        filtering_attributes: name,...  # optional; only meaningful on Update
        configuration: "..."            # optional unsecure config
        images:
          - alias: PreImage             # required; the key within the step
            image_type: pre             # required (pre|post|both)
            attributes: name,...        # optional; comma-separated logical names
            message_property_name: Target   # optional override
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

## Plug-ins: assembly, types, steps, and images

`plugins[]` declares one or more plug-in assemblies. Each entry identifies a
built DLL (`file`, resolved relative to the spec file) and optionally names the
assembly, its isolation mode, and its version override. Under it you declare the
`types[]` (the `IPlugin` classes) and `steps[]` (SDK message processing steps)
that should exist.

Convergent reconcile per component:

- **Assembly** — created when absent; the DLL content is PATCH-updated when a
  rebuilt binary differs from the live assembly; skipped when content is
  identical. The assembly name (or file stem if `assembly:` is omitted) is the
  convergent key.
- **Type** — registered when the declared `type_name` is not already present;
  skipped when it is. `type_name` is immutable once registered.
- **Step** (keyed by the unique `name`) — created when absent; runtime config
  fields (`stage`, `mode`, `rank`, `filtering_attributes`, `configuration`) are
  updated in place when they drift. Only spec-declared fields are reconciled.
  A **binding change** — a different `message`, `entity`, or `plugin_type` on an
  existing step — is classified `replace_blocked`: reported, no write, run exits
  1. The platform fixes bindings at creation; updating them requires a
  delete-and-recreate that `apply` does not perform automatically.
- **Image** (keyed by step + `alias`) — registered when absent; skipped when
  already present.

Plug-in components are not publishable, so a plugins-only apply does not issue
`PublishAllXml`. `--dry-run` is fully supported: a greenfield spec reports
components as `planned`; drift reports `updated` (with field-level `diff`) or
`replace_blocked`.

> On-prem metadata writes are synchronous, so a single apply registers a new
> assembly, its types, and its steps in one pass. On Dataverse (cloud) a
> newly-registered plug-in type can take a few seconds to become queryable, so a
> single apply that both registers a new type **and** a step binding to it may
> report the step as `failed` (the type is not yet resolvable); re-apply once it
> has propagated and the step lands (the already-created assembly and type are
> skipped). On-prem is the plug-in extensibility target.

## Stand up a table in one shot

```bash
crm --json apply -f project.yaml
```

Output is `{ok, data:{applied, updated, skipped, replace_blocked, pruned, planned, failed}, meta:{staged}}`.
Each entry is `{kind, name}`; `failed` and `replace_blocked` entries also carry
`error` / `reason`. A re-apply of an unchanged spec reports all matching
components under `skipped`. A re-apply of a changed spec reports in-place edits
under `updated`; immutable divergences under `replace_blocked` (and exits 1).
`pruned` entries carry `{kind, name, deleted}` (plus `reason` when a data-bearing
component is refused without `--allow-data-loss`, plus `would_prune: true` under
`--dry-run`). Without `--prune`, `pruned` is always empty.

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

## Prune org-extras

`--prune` opts in to solution-bounded deletion of components that are members of
the target solution but are no longer declared in the spec. A plain `apply`
never reads solution members and never deletes anything; pruning is entirely
opt-in.

**Eligible kinds (six):** `entity`, `attribute`, `view`, `security-role`,
`webresource`, `plugin-step`. Every other solution component type (option sets,
relationships, plug-in assemblies/types, forms) is out of scope.

**Gating:**

- Schema-only extras (`view`, `security-role`, `webresource`, `plugin-step`) are
  deleted on `--prune` after confirmation.
- Data-bearing extras (`entity`, `attribute`) destroy row data and are refused
  unless `--allow-data-loss` is also passed. Without it they appear in `pruned`
  with `deleted: false` and `reason: "data-bearing; pass --allow-data-loss to delete"`.
- Under `--json` or a non-TTY, `--prune` requires `--yes` (no interactive prompt).
- `--prune` requires a target solution (`solution:` block in the spec or
  `--solution`).
- Pruning is suppressed when the convergence phase itself has failures or
  replace-blocked components — a partial-failure run does not also delete org-extras.

**Always preview first:**

```bash
crm --dry-run --json apply -f project.yaml --prune
```

Dry-run reports all prune candidates under `pruned` with `deleted: false`; those
that would actually be deleted carry `would_prune: true`. No write is issued.
Pruning never triggers a publish.

**Worked example — remove a stale web resource and view from the solution:**

Spec `project.yaml` previously declared `contoso_/scripts/old.js` and a view
`Old Projects`; both are now removed from the spec. The solution still has them
as members.

```bash
# 1. Preview what would be pruned
crm --dry-run --json apply -f project.yaml --prune --solution ContosoCore

# 2. Apply with pruning (interactive confirmation on a TTY)
crm apply -f project.yaml --prune --solution ContosoCore

# 3. Under CI / --json there is no prompt, so pass --yes
crm --json apply -f project.yaml --prune --yes --solution ContosoCore
```

Example output (`data.pruned`):

```json
[
  {"kind": "webresource", "name": "contoso_/scripts/old.js", "deleted": true},
  {"kind": "view",        "name": "Old Projects",            "deleted": true}
]
```

A data-bearing extra refused without `--allow-data-loss`:

```json
{"kind": "attribute", "name": "contoso_legacycode", "deleted": false,
 "reason": "data-bearing; pass --allow-data-loss to delete"}
```

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
