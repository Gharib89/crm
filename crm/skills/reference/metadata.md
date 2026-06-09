# Metadata — schema introspection, picklists, dependencies, export, clone

Read schema, resolve option-set values before writing, preview deletes, and
round-trip entities to specs. Group: `metadata` (plus top-level `service-document`).
Flags/choices: `crm metadata --help`.

## Browse metadata

```bash
crm --json metadata entities --custom-only --top 20
crm --json metadata attributes account
crm --json metadata attribute account industrycode

# --expect ATTR=VALUE asserts a field on the returned record (repeatable, AND-gated,
# stringified); a mismatch exits 1. See "Verify a metadata change landed" below.
crm --json metadata attribute account industrycode --expect AttributeType=Picklist
```

## Picklist / option set values (critical before writing a record)

A record write with a bad option-set value is rejected by the server — **look the
values up first.**

```bash
crm --json metadata picklist account industrycode
# data: raw {"OptionSet": {"Options": [{"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Accounting"}}}, ...]}}
# meta.options: flattened [{"value": 1, "label": "Accounting"}, ...] — same for `metadata get-optionset <name>`
```

`meta.options` (JSON mode only) flattens the nested labels to `[{value, label}]` so
you need not dig through `Label.UserLocalizedLabel.Label`; raw `data` is unchanged.
Boolean attributes have no `Options` array (`TrueOption` / `FalseOption` instead), so
`meta.options` is empty for them — read the raw `TrueOption`/`FalseOption` fields.

## Write-readiness brief — one call before writing a record

```bash
crm --json metadata describe new_project
# data: { entity_set_name, primary_id, primary_name, writable_attributes: [
#   { logical_name, attribute_type, required_level,
#     # lookups:                bind_key:"new_AccountId@odata.bind", targets:[{logical,set_name}]
#     # picklist/state/status:  options:[{value,label}]
#     # global-bound picklist:  + global_optionset_id (GUID) } ] }
```

One read-only call that consolidates everything needed to build a valid create/update
payload: the entity set name, primary id/name, every writable column with its required
level, lookup `@odata.bind` keys + resolvable targets, and inline option values.
**Prefer this over chaining `attributes` + `picklist` + `relationships` by hand** —
it hands you the exact `bind_key` and `global_optionset_id` you'd otherwise assemble
manually (see the `@odata.bind` notes in `reference/records.md`).

## Entity-definition cache (speed up repeated agent calls)

Pass `--cache-metadata` (or set `CRM_CACHE_METADATA=1`) to serve `metadata entities`
from a persistent per-profile on-disk cache instead of a live fetch — the recommended
form for agent loops that resolve entity set names repeatedly:

```bash
crm --json --cache-metadata metadata entities
# meta.cache: "hit" / "miss" / "refreshed"

crm --json --refresh-metadata metadata entities   # force a fresh fetch, overwrite cache
crm --json metadata cache-clear                    # delete the active profile's cache
```

Cache mode returns **only the 2-field rows** (LogicalName / EntitySetName) — enough to
resolve entity set names. Cache file: `~/.crm/cache/<profile>/entitydefs.json` (root
overridable with `CRM_HOME`), TTL ~15 min. Any metadata write auto-invalidates it.
Read-only schema only — records and secrets are never cached.

## Export a live entity as an apply spec (round-trip)

```bash
crm metadata export-spec new_project --with-views --with-relationships -o project.yaml
crm apply -f project.yaml   # re-create / idempotently re-apply in any environment
```

`export-spec` reads the entity over the Web API (pure GETs) and emits a `crm apply -f`
desired-state spec (see `reference/authoring.md`). With `-o FILE` it writes bare YAML
directly consumable by `apply`; without `-o` the spec is wrapped in the JSON envelope.

It captures: entity definition, primary-name attribute, all custom apply-creatable
columns, referenced global option sets, and (with flags) relationships and views.
Publisher/solution are **not** emitted — supply them via `--solution` on `apply`, or
edit the YAML. **Fidelity caveats** (these silently lose information on round-trip):

- A string column whose live format is `Json` or `RichText` is re-created as plain `Text`.
- A datetime column's format is NOT captured (re-created with the default format).
- A polymorphic (multi-target) lookup is exported with its **first target only** and
  re-created as a single-target lookup.
- Relationship `cascade` / `associated_menu` are captured but re-created with default
  cascade/menu.

`apply` ignores unknown keys, so the spec always stays apply-consumable.

## Clone a custom entity

Duplicate a custom entity under a new schema name. The bare clone copies entity
definition, custom attributes (lookups recreated pointing at the same parent tables),
and reuses referenced global option sets by name. Forms, views, workflows, and charts
are opt-in.

```bash
# skeleton only (entity + attributes + lookups + reused option sets)
crm --json metadata clone-entity new_project cwx_TicketClone --display "Ticket Clone"

# everything cloneable over the API
crm --json metadata clone-entity new_project cwx_TicketClone --with-all --solution MySolution

# opt-in flags
crm --json metadata clone-entity new_project cwx_TicketClone \
    --with-forms --with-views --with-workflows --with-charts
```

**Not cloned (Web API limits):**

- **Ribbon** — `RibbonDiffXml` has no Web API write path (solution import only). The
  result carries a `ribbon_note` confirming this.
- **N:N relationships**, and 1:N where the source is the *parent* (referenced) side —
  cloning those would add lookups on *other* tables.
- **Polymorphic / Customer lookups** — only single-target lookups come across.
- **Personal charts** (`userqueryvisualization`) — not cloned; public system charts are
  handled by `--with-charts`.

`--with-workflows` copies every classic workflow/business rule (`type=1`) whose
primary entity is the source, including managed ones (no "is custom" filter available).
Actions, BPFs, dialogs, and modern flows are skipped (reported under
`skipped_workflows`). On Unified Interface a cloned form may need adding to the
model-driven app's form list to be visible.

## Preview dependencies before deleting a metadata component

```bash
# What would block deleting an entity
crm --json metadata dependencies cwx_ticket

# What would block deleting a column (dotted entity.attribute)
crm --json metadata dependencies cwx_ticket.cwx_priority --kind attribute

# What depends on a global option set
crm --json metadata dependencies cwx_status --kind optionset --for dependents
```

Returns `{can_delete, blockers[], metadata_id, component_type, kind, for}`; each
blocker carries `dependent_type`, `dependent_id`, `dependent_parent_id`,
`required_type`, `dependency_type`. `--for delete` (default) uses
`RetrieveDependenciesForDelete`; `--for dependents` uses `RetrieveDependentComponents`.
Read-only. To fold dependency info into a delete result non-destructively:

```bash
crm --json --dry-run metadata delete-attribute cwx_ticket cwx_priority --yes --check-dependencies
```

`--check-dependencies` is available on `delete-entity`, `delete-attribute`,
`delete-relationship`, and `delete-optionset`.

## Verify a metadata change landed (`--expect`)

A metadata create/publish can take a moment to propagate. Poll until the definition
reflects the change, then retry if it hasn't:

```bash
crm metadata add-attribute new_widget --kind string \
    --schema-name new_Label --display Label --max-length 100 \
  && crm solution publish-all \
  && crm --json metadata attribute new_widget new_label --expect AttributeType=String \
  || echo "attribute not ready yet — retry"
```

`--expect ATTR=VALUE` is repeatable, AND-gated, and stringified (each pair passes only
if `str(record[ATTR]) == VALUE`; a missing key never matches). The first mismatch exits
**1** with `{ok:false, error:"Expectation failed: …", meta:{attr, expected, actual}}`,
so a shell `||` branch — or an agent — can branch and retry. A malformed `--expect`
(no `=`) is a usage error (exit 2) raised before any HTTP. Attribute logical names are
lowercase (`new_label`); the schema name is PascalCase (`new_Label`). The same flag on
`entity get` asserts a write landed on the record side (e.g. `--expect statecode=1`,
checked against the full record before any `--minimal` projection).

## Inspect the server's entity sets

```bash
crm --json service-document
# returns {"value": [{"name": "accounts", "url": "accounts", ...}, ...]}
```
