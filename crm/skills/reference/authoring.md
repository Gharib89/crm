# Schema authoring — apply, scaffold, views, stage-then-publish

Stand up tables, columns, option sets, views, web resources, security roles, and
plug-in assemblies / types / steps / images — declaratively or imperatively.
Commands: top-level `apply`, `scaffold table`, `view create`, the `metadata
create-*` and `update-*` verbs, and the publish flow. Flags/choices: `crm
describe apply`, `crm <group> --help`. **To change existing schema:** re-apply
the spec (`apply` reconciles matching components — equal → skip, updatable drift
→ update in place, destructive divergence → refuse) or use the imperative
`metadata update-attribute` / `update-entity` / `update-optionset` /
`update-relationship` verbs.

## Declarative apply — `apply -f spec.yaml`

Stand up a whole table from one YAML/JSON spec instead of many imperative commands.
`apply` runs the metadata and plug-in cores in dependency order (publisher → solution →
entities → option sets → attributes → relationships → views → web resources → security
roles → plug-ins) and **publishes once at the end** — only when a publishable component
changed (security roles and plug-in components are not publishable, so an apply that
touches only those does not publish).

`apply` is **convergent** — a component that already exists is reconciled against
the spec, not blindly skipped. Three outcomes per component:

- **equal** → `skipped` (idempotent re-apply, no write).
- **updatable divergence** → updated in place → counted in `updated`. Updatable:
  entity display name / display-collection name / description; attribute display
  name, description, required level, and string `max_length` growth; adding
  declared options to a global option set.
- **immutable/destructive divergence** → `replace_blocked`: reported, **no write**,
  run exits `ok=false` / exit 1. Blocked cases: entity ownership change, attribute
  data-type change. A `replace_blocked` component does not abort siblings — the rest
  of the spec still reconciles.

Reconciliation also runs under `--dry-run`, read-only (writes suppressed by the
reads-execute rule), so a dry-run is a full drift report: every declared
component is classified into `planned` (would create), `updated` (would update,
with a field-level `diff`), `replace_blocked`, or `pruned` — no write issued.

```bash
crm --json apply -f project.yaml              # converge, publish once
crm --dry-run --json apply -f project.yaml    # drift report: planned/updated/replace_blocked/pruned
crm --stage-only --json apply -f project.yaml # converge without publishing
```

Emits `{ok, data:{applied, updated, skipped, replace_blocked, pruned, planned, failed}, meta:{staged}}`;
each entry is `{kind, name}`. `failed` and `replace_blocked` entries also carry
`error` / `reason`. `pruned` is reserved (always empty today — opt-in pruning is a
future slice). **Metadata writes are non-transactional: a hard failure aborts the
remaining steps and leaves staged-but-unpublished residue.** A new table's views may
report `planned` until the first publish assigns its ObjectTypeCode — **re-apply to
land them.**

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
      - {kind: memo,     schema_name: contoso_Notes,    display_name: Notes, max_length: 2000}
      - {kind: picklist, schema_name: contoso_Priority, display_name: Priority, optionset_name: contoso_priority}
      - {kind: lookup,   schema_name: contoso_Owner,    display_name: Owner, target_entity: systemuser}
    views:
      - {name: Active Projects, columns: [contoso_name, contoso_code]}
webresources:
  - name: contoso_/scripts/project.js   # unique name; webresourcetype inferred from .js
    file: scripts/project.js            # path relative to the spec file
    display_name: Project Script        # optional
security_roles:
  - name: Contoso Project Manager
    privileges:
      - {access: [read, write, create], entities: [contoso_project], depth: deep}
      - {privilege_names: [prvReadSystemForm], depth: global}
plugins:
  - assembly: Contoso.Plugins           # optional; defaults to DLL file stem
    file: bin/Contoso.Plugins.dll       # path relative to the spec file
    isolation_mode: sandbox             # optional (none|sandbox)
    types:
      - type_name: Contoso.Plugins.AccountHandler   # fully-qualified class; the convergent key
    steps:
      - name: Contoso Account Handler   # unique stable key
        message: Create
        plugin_type: Contoso.Plugins.AccountHandler
        entity: account                 # optional; omit for message-level
        stage: postoperation            # optional (prevalidation|preoperation|postoperation)
        images:
          - alias: PreImage
            image_type: pre             # pre|post|both
```

In a spec attribute block, `string` and `memo` `max_length` is optional — omit it and
the create defaults to 100 / 2000 (matching the `scaffold` / column-shorthand path). An
explicit `max_length` is honored verbatim; `max_length` on any other kind is rejected.

**Security role convergence gotcha — baseline privileges and removal-only no-op.**
Dataverse auto-grants every role immovable baseline privileges (e.g. SharePoint
document management) that `ReplacePrivilegesRole` cannot remove — apply treats them
as invisible and will not block on them. A privilege *dropped* from the spec is only
removed if another declared privilege also drifts in the same run (triggering a fresh
replace). A removal-only change where all remaining declared privileges are already
satisfied is a convergent no-op; use `crm security set-role-privileges` to force it.

**Plug-in step binding is immutable — `replace_blocked` on message/entity/type change.**
The platform fixes a step's `message`, `entity`, and `plugin_type` at creation; there
is no PATCH path to change them. If a declared step's binding drifts from the live
record, apply classifies it `replace_blocked` (reported, no write, exits 1). To
rebind a step: unregister it manually (`crm plugin unregister-step`) then re-apply.

**Plug-in components are not publishable** — a plugins-only apply never issues
`PublishAllXml`.

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

Emits the same `{applied, updated, skipped, replace_blocked, pruned, planned, failed}` envelope as `apply`.

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

`--query-type` (see `--help` for the choices) selects the savedquery type; the
default is a public grid view. Two non-obvious effects: picking the quick-find
type also flips `isquickfindquery` on the row (so the view backs global search,
not the grid picker), and the existence guard keys on name+entity+**type** — the
same name can coexist across types, and `--if-exists skip` only matches a prior
view of the same type. **Gotcha:** `view list` shows only public views, so a
non-public view you create this way will not appear there — capture its
`savedqueryid` from the `view create` output if you need to edit it later.

### Edit an existing view's columns — `view edit-columns`

```bash
crm --json view edit-columns account "All Accounts" \
    --add telephone1:120 --remove fax --width name:200
crm --json view edit-columns account "All Accounts" \
    --reorder name,telephone1,emailaddress1
```

**Mismatch invariant.** `--add` writes both the layoutxml `<cell>` and the fetchxml
`<attribute>` in one PATCH — a cell without a matching attribute leaves a column with
no data, so the CLI always keeps them coupled. Likewise `--remove` drops both. The
primary-key cell+attribute are protected and cannot be removed.

**Ambiguous name → resolve by GUID.** The savedquery table has no alternate key.
`edit-columns` resolves by `name + returnedtypecode + querytype`; if more than one
row matches, the command errors. Run `crm --json view list <entity>` to get the
`savedqueryid`, then pass that GUID as the `<view>` argument.

**Non-public views.** Pass `--query-type` (advanced-find, associated, quick-find,
lookup) to target a non-public view. `view list` shows only public views.

**Publish-then-read-back.** Under `--publish` (the default) the command publishes
and then GETs the view back to confirm the edit landed. Under `--no-publish` the
read-back is skipped — a subsequent GET returns the *published* (pre-edit) snapshot
until you publish. `layoutjson` is cleared on every column edit so the platform
rebuilds it from the new layoutxml (a stale layoutjson drives the modern grid with
the old columns).

**Managed-layer warning.** Editing an out-of-box or managed view creates an
unmanaged layer that a solution upgrade may revert. The `--help` text carries this
warning too; it's repeated here because it is the most common surprise.

### Set a view's sort order — `view set-order`

```bash
crm --json view set-order account "All Accounts" \
    --order "name asc" --order "createdon desc"
crm --json view set-order account "All Accounts" --add-order "modifiedon desc"
crm --json view set-order account "All Accounts" --clear-order
```

Only the entity's direct `<order>` children are touched — `<filter>`, `<condition>`,
and `<link-entity>` elements are left intact. Order attributes are validated against
live metadata before any write. Same ambiguous-name, managed-layer, and
publish-then-read-back notes as `edit-columns`.

### Add FetchXML filter conditions — `view add-filter`

```bash
crm --json view add-filter cwx_ticket "Active Tickets" \
    --condition "statecode eq 0"
crm --json view add-filter cwx_ticket "Active Tickets" \
    --condition "cwx_priority in 1 2 3" --condition "cwx_severity ne 3"
crm --json view add-filter cwx_ticket "Active Tickets" \
    --condition "cwx_resolvedon null"
```

Conditions are appended to the entity-level `<filter>` (created if absent).
`<link-entity>` filters and existing conditions are never touched. The condition
attribute is validated against live metadata before any write.

**Operator cardinality** — the non-obvious part: no-value operators (`null`,
`not-null`, `today`, `eq-userid`, …) take no value tokens;
`between`/`not-between` take exactly two; `in`/`not-in`/`contain-values`/
`not-contain-values` take a list (emitted as child `<value>` elements);
all other operators take a single value (remaining tokens are joined, so
`name eq Contoso Ltd` works). Wrong cardinality is rejected before any write.

Same ambiguous-name, managed-layer, and publish-then-read-back notes as
`edit-columns`.

### Remove FetchXML filter conditions — `view remove-filter`

```bash
crm --json view remove-filter cwx_ticket "Active Tickets" \
    --condition "statecode eq 0"
# disambiguate when attribute+operator match multiple conditions:
crm --json view remove-filter cwx_ticket "Active Tickets" \
    --condition "cwx_priority in 1 2 3"
```

Matched on attribute + operator; supply values to disambiguate. No match or
multiple matches → error. The attribute need not still exist on the entity (so
filters on deleted columns can be cleaned up). An empty `<filter>` after
removal is pruned. Link-entity filters are never searched.

Same ambiguous-name, managed-layer, and publish-then-read-back notes as
`edit-columns`.

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
