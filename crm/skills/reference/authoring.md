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
entities → option sets → attributes → relationships → views → web resources → forms →
security roles → plug-ins → apps) and **publishes once at the end** — only when a publishable
component changed (security roles and plug-in components are not publishable, so an apply
that touches only those does not publish).

`apply` is **convergent** — a component that already exists is reconciled against
the spec, not blindly skipped. Three outcomes per component:

- **equal** → `skipped` (idempotent re-apply, no write).
- **updatable divergence** → updated in place → counted in `updated`. Updatable:
  entity display name / display-collection name / description, and enabling
  `has_notes` / `has_activities` (`false → true`); attribute display name,
  description, required level, and string `max_length` growth; adding declared
  options to a global option set; relationship cascade configuration,
  associated-menu (label / behavior / order), and `is_hierarchical`, plus the
  relationship-backed lookup column's display name, description, and required
  level — surfaced as one merged `updated` entry per relationship block; view
  `description`, `is_default`, `columns`, `filter_active`, `order_by`,
  `order_desc` (record PATCH of regenerated fetchxml / layoutxml).
- **immutable/destructive divergence** → `replace_blocked`: reported, **no write**,
  run exits `ok=false` / exit 1. Blocked cases: entity ownership change; explicit
  `has_notes` / `has_activities` disable (`true → false` — enable-only); `is_activity`
  change (identity); attribute data-type change; a relationship's
  referenced/referencing-entity or lookup-column change, or a relationship-type
  mismatch (live is N:N, spec expects 1:N). A `replace_blocked` component does not
  abort siblings — the rest of the spec still reconciles.

**Reconciled vs. create-only spec keys.** The full builder keyword surface is
expressible in the spec. **Reconciled on re-apply:**

- **Relationship** — `cascade_assign/delete/reparent/share/unshare/merge`,
  `menu_label/behavior/order`, `is_hierarchical`, and the lookup column's
  `lookup_display`, `lookup_description`, `required` (reconciled via the referencing
  attribute; one merged `updated` entry per relationship block). Replace-blocked
  identity divergences: relationship type mismatch (live is N:N, spec expects 1:N),
  or a referenced/referencing-entity or lookup-column (`lookup_schema`) change.
- **Entity** — `has_notes` / `has_activities` (`false → true`, enable-only;
  `true → false` is `replace_blocked`). `is_activity` divergence is `replace_blocked`
  (identity change). Only spec-declared fields drift.
- **View** — existing view matched by `(entity, name, query_type)` is reconciled in
  place: `description`, `is_default`, `columns` (regenerates layoutxml),
  `filter_active`, `order_by`, `order_desc` (regenerate fetchxml). A changed `name`
  or `query_type` has no live match → falls to the create path (new view made, old
  one left for `--prune`). Ambiguous match (>1 row) → `skipped` with a reason.
- **Attribute** — `display_name`, `description`, `required` level, and string/memo
  `max_length` **growth** only (a shrink is left as-is). A data-type change is
  `replace_blocked` (identity). Lookup/customer kinds are relationship-backed and
  reconciled via the relationship block, not here.
- **Option set** — spec-declared options the live global set lacks are inserted
  (matched by explicit `value`; an auto-valued option is create-path only, else a
  re-apply would re-insert it). Existing option labels and removals are not reconciled.
- **Form** (`forms:` nested under an entity) — **converges the entity's
  platform-generated main form** (ADR 0024); `apply` never forges a form from
  scratch. A block declares `tabs[]` → `sections[]` → `fields[]` (attribute logical
  `name` → control resolved from its type), plus `libraries[]` (JS web-resource
  names, must already exist) and `handlers[]` (`event` onload/onsave/onchange,
  `function`, `library`; `onchange` also needs `field`). Optional `name` picks
  among the entity's main forms (default: the primary). Convergence is **additive +
  idempotent**: a declared component absent from the form is added (a purely additive
  form → `applied`, dry-run → `planned`); a form already satisfying the declaration
  is `skipped`. A component **present but drifted** is converged in place and the
  form routes to `updated`: a tab/section label, a field's tab+section placement
  (relocated, not duplicated), the relative order of the declared tabs/sections, and
  a handler's `enabled`/`pass_context` flags. A field control `classid` is
  **create-only** — never retyped in place. A declared `name` that resolves to no
  single existing main form is an **identity/ownership divergence** → `replace_blocked`
  (no write, exit 1, siblings still reconcile): the stance is *converge an existing
  main form*, never forge or rewrite the wrong one. A greenfield entity whose main
  form is not yet readable is `planned` — re-apply to land it. Forms are **out of
  scope for `--prune`**. The entry carries `components: [{kind, name, …}]`; a
  converged component adds `change: "converged"` and (under `--dry-run`) a `diff`.
- **Model-driven app** (`apps:` top-level, ADR 0024) — an absent app is **created**
  through the app-module + sitemap builders: `components[]` (`{kind, id}`, kind
  view/chart/form/dashboard/bpf/sitemap) bound via `AddAppComponents`, and a nested
  `sitemap` (`areas[] → groups[] → subareas[]`, subarea `entity` = logical name) set
  wholesale and auto-linked to the app by its `unique_name`. **Tables reach the app
  via a sitemap subarea, not `components[]`.** Dry-run on an absent app → `planned`.
  An **existing** app is **reconciled** (#796): the declared `components[]` set is
  converged against the live app's bound components (over view/chart/form/dashboard/bpf
  only — the sitemap and a table's implicit binding are never added/removed here) —
  a declared-but-unbound component is added, a bound-but-undeclared one is removed;
  the sitemap converges by **whole-document replacement** — the live `sitemapxml` is
  replaced wholesale whenever it differs from the built document (an app with no
  linked sitemap yet gets one created). Unchanged → `skipped`; any component or
  sitemap change → `updated`, the entry carrying a `components: [{kind, id, change:
  "added"|"removed"}]` list and/or `sitemap: "converged"|"added"`. A **managed** app
  is `replace_blocked` (no write, exit 1, siblings still reconcile) — converge it
  through its parent solution instead. `--dry-run` reads the live app and reports
  the full drift as `updated`, every write suppressed. Apps/sitemaps are publishable
  (defer to the end-of-run publish; `--stage-only` honoured) and **out of scope for
  `--prune`**. The `applied`/`updated` entry carries the live `appmoduleid` /
  `sitemapid`. Gotcha: a freshly created appmodule is not reliably GET-retrievable
  until published — the create path treats the read-back miss as non-fatal (operate
  on the returned id, don't re-query), and on an org where that window persists past
  publish, a re-apply that would reconcile an existing app instead reports it
  `skipped` (the app can't be read back to diff, so there is nothing to converge).

**Create-only** (re-applying an existing component does not yet reconcile these):
attribute — `default_value`, `true_label/false_label`, `min_value/max_value`,
`max_size_kb`, `auto_number_format`, `behavior_name`, `relationship_schema`; entity
— `primary_attr_max_length`, `data_provider_id/data_source_id`,
`external_name/external_collection_name`.

`export-spec` emits the subset of these keys that map to live Web API fields —
relationships emit flat `cascade_assign/delete/…`, `menu_behavior/label/order`,
`is_hierarchical`, and `lookup_description` (the lookup column's `lookup_schema`
carries the referencing attribute's true schema-name casing, so the column
round-trips with matching casing); attributes emit `auto_number_format`,
`min_value`/`max_value`, `max_size_kb`, `behavior_name`; entity emits `has_notes`,
`has_activities`, `primary_attr_max_length`, `description`; views emit
`filter_active`, `order_desc`, `description`; global option sets emit
`description`. Fields equal to platform defaults (and empty descriptions) are
omitted.

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
`error` / `reason`. `pruned` entries carry `{kind, name, deleted}` (+ `reason`
when a data-bearing component is refused, + `would_prune: true` under `--dry-run`).
`pruned` is populated under `--dry-run` (candidates, `deleted: false`) and `--prune`
(deletions); a plain real-run apply with neither leaves it empty.
**Metadata writes are non-transactional: a hard failure aborts the remaining steps
and leaves staged-but-unpublished residue.** A new table's views may report
`planned` until the first publish assigns its ObjectTypeCode — **re-apply to land
them.**

### Approval-gated apply (plan → verify → execute)

For an unattended/agent-driven apply where "what was approved must be what runs",
serialize the dry-run drift report as a **plan** and later execute it only if the
org has not drifted since:

```bash
crm --dry-run --json apply -f project.yaml -o plan.json  # 1. plan (review plan.json)
crm --dry-run --json apply --from-plan plan.json         # 2. verify (CI pre-check)
crm --json apply --from-plan plan.json                   # 3. execute the approved plan
```

- `--from-plan` is mutually exclusive with `-f`. It **replays** the plan's intent
  (`--prune` / `--allow-data-loss` / `--stage-only`) from the plan header — passing
  any of them (or `-o`) alongside `--from-plan` is a usage error (exit 2).
- Before executing, it recomputes the drift report and compares at the **action
  level** (component set + verdict + each `updated` component's changed-field set;
  live values are not byte-compared). **Any divergence → stale plan: zero writes,
  `ok=false`, exit 1**, with `data.divergences` = `[{kind, name, plan, live}]`. The
  fix is always re-plan, not a weaker gate.
- `--dry-run --from-plan` is **verify mode**: it reports `data.plan_valid`
  (`true`/`false`) and writes nothing.
- Pre-flight **refuses** (exit 1) a plan whose `plan_format` is unknown, whose
  `organization_id` ≠ the live WhoAmI, that carries `replace_blocked`/`failed`
  components, or whose pinned payloads are missing/changed. A URL or CLI-version
  mismatch is only a `meta.warnings` note. **Payload `file`s resolve relative to the
  plan file's directory** — keep referenced web-resource/DLL files beside the plan.
- Residual TOCTOU: the gate shrinks the window to verify-to-write but cannot close
  it (metadata writes are not transactional).

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
`source_type` (`simple` / `calculated` / `rollup`) with `formula_definition` (XAML
string) creates a rollup or calculated column — mirrors `metadata add-attribute --type`.
`source_type: calculated` / `rollup` requires `formula_definition` and is rejected on
`lookup`/`customer` kinds. Omitting `source_type` creates a plain column. Formula
drift is **not** reconciled — the reconcile pass ignores `formula_definition`.

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

## Pruning org-extras — `--prune`

`--prune` opts in to solution-bounded deletion of components that are **members of
the target solution but absent from the spec**. A plain `apply` never reads
solution members; pruning is strictly opt-in.

**Six eligible kinds:** `entity`, `attribute`, `view`, `security-role`,
`webresource`, `plugin-step`. All other solution component types are out of scope.

**Gating — what `--help` cannot convey:**

- **Schema-only kinds** (`view`, `security-role`, `webresource`, `plugin-step`) —
  deleted after confirmation.
- **Data-bearing kinds** (`entity`, `attribute`) — refused unless `--allow-data-loss`
  is also passed; they appear in `pruned` with `deleted: false` and a `reason` field.
- **`--json` / no-TTY** — for a **real** prune the interactive prompt is
  unavailable, so `--yes` is required or the run errors before doing anything. A
  `--dry-run` preview deletes nothing, so it needs no `--yes`.
- **Scoped to the spec's mandatory `solution:` block.** Every spec must declare a
  top-level `solution: {unique_name: ...}` (see the YAML block above) — `apply` has
  no `--solution` flag; the target is always explicit.
- **Suppressed on partial failure** — if the convergence phase produces any `failed`
  or `replace_blocked` entries, pruning is skipped entirely. Fix the spec, re-apply,
  then prune.
- **Never publishes** — a prune deletion triggers no publish (the end-of-run
  publish gate covers only created/updated components).

**Always dry-run before a real prune:**

```bash
crm --dry-run --json apply -f project.yaml --prune
```

Candidates appear in `data.pruned` with `deleted: false`; those that *would* be
deleted carry `would_prune: true`. No write is issued.

**JSON contract for `pruned` entries:**

```json
{"kind": "webresource", "name": "contoso_/scripts/old.js", "deleted": true}
{"kind": "view",        "name": "Old Projects",            "deleted": false, "would_prune": true}
{"kind": "attribute",   "name": "contoso_legacycode",      "deleted": false,
 "reason": "data-bearing; pass --allow-data-loss to delete"}
```

**Non-interactive (CI / agent) pattern:**

```bash
crm --json apply -f project.yaml --prune --yes
# Add --allow-data-loss only when entity/attribute deletion is intentional
```

## Scaffold a table — `scaffold table`

Quick one-liner to create an entity + N columns in a single publish, through the same
`apply` engine. Each resource is `if_exists=skip` — re-running is a no-op.

```bash
crm --json scaffold table "Project" --solution ContosoCore \
  --column "Name:string:max_length=200,required=ApplicationRequired" \
  --column "Due Date:datetime" \
  --column "Owner:lookup:target_entity=systemuser" \
  --column "Priority:picklist:optionset_name=new_priority"

crm --dry-run --json scaffold table "Project" --solution ContosoCore --column "Name:string"   # plan only
crm --stage-only --json scaffold table "Project" --solution ContosoCore --column "Name:string" # no publish
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
crm --json view create contoso_ticket --name "Active Tickets" --otc 10127 \
    --column "contoso_name:220" --column "contoso_priority:120" \
    --filter-active --solution ContosoCore --if-exists skip
```

The LayoutXml `object` attribute is the entity **ObjectTypeCode (OTC)** — get it from
`metadata entity <name>` (see `reference/metadata.md`). `--column` is repeatable
`'logical[:width]'` with order preserved.

`--order-by` takes an optional `asc`/`desc` suffix (same `$orderby` idiom as
`query odata --order-by`): `--order-by createdon` is ascending, `--order-by 'createdon desc'`
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
    --add telephone1:120 --remove fax --width name:200 --solution ContosoCore
crm --json view edit-columns account "All Accounts" \
    --reorder name,telephone1,emailaddress1 --solution ContosoCore
```

Every `view` editor verb (`edit-columns`, `set-order`, `add-filter`,
`remove-filter`) is solution-scoped.

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

**Publish-then-read-back.** `edit-columns` **stages** by default (no publish).
Pass `--publish` to also publish and GET the view back to confirm the edit
landed; without it the read-back is skipped and a subsequent GET returns the
*published* (pre-edit) snapshot until you publish. `layoutjson` is cleared on
every column edit so the platform rebuilds it from the new layoutxml (a stale
layoutjson drives the modern grid with the old columns).

**Managed-layer warning.** Editing an out-of-box or managed view creates an
unmanaged layer that a solution upgrade may revert. The `--help` text carries this
warning too; it's repeated here because it is the most common surprise.

### Set a view's sort order — `view set-order`

```bash
crm --json view set-order account "All Accounts" \
    --order "name asc" --order "createdon desc" --solution ContosoCore
crm --json view set-order account "All Accounts" --add-order "modifiedon desc" --solution ContosoCore
crm --json view set-order account "All Accounts" --clear-order --solution ContosoCore
```

Only the entity's direct `<order>` children are touched — `<filter>`, `<condition>`,
and `<link-entity>` elements are left intact. Order attributes are validated against
live metadata before any write. Same ambiguous-name, managed-layer, and
publish-then-read-back notes as `edit-columns`.

### Add FetchXML filter conditions — `view add-filter`

```bash
crm --json view add-filter contoso_ticket "Active Tickets" \
    --condition "statecode eq 0" --solution ContosoCore
crm --json view add-filter contoso_ticket "Active Tickets" \
    --condition "contoso_priority in 1 2 3" --condition "contoso_severity ne 3" --solution ContosoCore
crm --json view add-filter contoso_ticket "Active Tickets" \
    --condition "contoso_resolvedon null" --solution ContosoCore
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
crm --json view remove-filter contoso_ticket "Active Tickets" \
    --condition "statecode eq 0" --solution ContosoCore
# disambiguate when attribute+operator match multiple conditions:
crm --json view remove-filter contoso_ticket "Active Tickets" \
    --condition "contoso_priority in 1 2 3" --solution ContosoCore
```

Matched on attribute + operator; supply values to disambiguate. No match or
multiple matches → error. The attribute need not still exist on the entity (so
filters on deleted columns can be cleaned up). An empty `<filter>` after
removal is pruned. Link-entity filters are never searched.

Same ambiguous-name, managed-layer, and publish-then-read-back notes as
`edit-columns`.

## Stage many changes, then publish once

Batch flagless customization writes, then publish once at the end (the
staged-writes contract in SKILL.md):

```bash
crm metadata add-attribute new_widget \
    --kind string --schema-name new_Label --display Label --max-length 100 --solution ContosoCore
crm metadata create-optionset --name new_priority --display Priority \
    --option 1:Low --option 2:High --solution ContosoCore
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
