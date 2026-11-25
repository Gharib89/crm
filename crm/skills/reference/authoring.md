# Schema authoring — declarative apply and the spec format

Stand up tables, columns, option sets, views, web resources, security roles, and
plug-in assemblies / types / steps / images — declaratively or imperatively.
Commands: top-level `apply`, the `metadata create-*` and `update-*` verbs.
Flags/choices: `crm describe apply`, `crm <group> --help`. **To change existing schema:** re-apply
the spec (`apply` reconciles matching components — equal → skip, updatable drift
→ update in place, destructive divergence → refuse) or use the imperative
`metadata update-attribute` / `update-entity` / `update-optionset` /
`update-relationship` verbs.
Approval-gated apply and `--prune`: `reference/authoring-gated.md`. `scaffold table`
and stage-then-publish: `reference/authoring-scaffold.md`. Views: `reference/views.md`.

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
  `max_length` **growth** only. A data-type change (identity) and a `max_length`
  shrink (it truncates data) are `replace_blocked`: reported, no write for that
  attribute. Lookup/customer kinds are relationship-backed and reconciled via the
  relationship block, not here.
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
  **create-only** — never retyped in place. The platform's default tab and section
  (labelled `General`, no `name`) are reachable by label: a declared tab whose
  `name` matches nothing **adopts** the single unnamed tab with the same label
  (`label`, else `name`; case-insensitive), gets the declared `name` written onto
  it, and converges there (`updated`, diff `name: {old: null, new: <name>}`);
  a declared section adopts the single unnamed section with its label inside its
  tab the same way. Zero or several same-label unnamed candidates adopt nothing
  (the tab/section is added), and a named tab/section is never adopted. An added
  tab with declared sections gets no empty `<name>_section` starter. A declared
  `name` that resolves to no single existing main form is an **identity/ownership
  divergence** → `replace_blocked`
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
  An **existing** app is **reconciled**: the declared `components[]` set is
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
  `sitemapid`. Gotcha: a **created** app with a sitemap bound is also app-published
  individually after the end-of-run publish, so it's immediately GET-visible in the
  `appmodules` collection — a bare app (no sitemap) can't pass that app-scoped
  publish and stays unpublished/invisible until a later run completes it.

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

### Spec example and per-kind rules

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
