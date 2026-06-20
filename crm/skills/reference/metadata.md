# Metadata — schema introspection, picklists, dependencies, export, clone

Read schema, resolve option-set values before writing, preview deletes, and
round-trip entities to specs. Group: `metadata` (plus top-level `service-document`).
Flags/choices: `crm metadata --help`.

## Browse metadata

```bash
crm --json metadata entities --custom-only --top 20
# --managed-only adds IsManaged eq true; --filter "<odata>" appends a raw $filter
# (both AND-combined with --custom-only). Rejected with --cache-metadata.
crm --json metadata entities --managed-only --filter "IsActivity eq true"
crm --json metadata attributes account
crm --json metadata attribute account industrycode

# --expect ATTR=VALUE asserts a field on the returned record (repeatable, AND-gated,
# stringified); a mismatch exits 1. See "Verify a metadata change landed" below.
crm --json metadata attribute account industrycode --expect AttributeType=Picklist
```

`metadata attributes` returns `data: [...]` where each item carries:
`LogicalName`, `SchemaName`, `AttributeType`, `IsCustomAttribute`,
`IsValidForCreate`, `IsValidForUpdate`, `IsValidForRead` (booleans), and
`RequiredLevel` (string: `"None"`, `"ApplicationRequired"`, `"SystemRequired"`,
`"Recommended"` — the literal `"None"` is a string, not JSON `null`; `null`
only when the value is genuinely absent). `RequiredLevel` is flattened from the
server's nested `{"Value": "..."}` object — use `item["RequiredLevel"]` directly.

## Alternate keys (`metadata keys`)

```bash
crm --json metadata keys account
```

Returns `data: [{logical_name, schema_name, key_attributes, index_status}]`.
Empty `data: []` means no alternate keys are defined — not an error.
`index_status` values: `Active`, `Pending`, `Failed`, `InProgress`.

Create/drop the key with `metadata create-key <entity>` /
`metadata delete-key <entity> <key>` (a `metadata create-*`/`delete-*` write verb
— see `reference/customization-lifecycle.md`). A freshly created key's index builds
asynchronously (`index_status` `Pending`), and `entity upsert --key` /
`data import --mode upsert --key` 404 against it until it reaches `Active` — poll
`metadata keys` to confirm before upserting (see `reference/records.md`).

When `entity create` or `entity update` hits an alternate-key collision (HTTP 412,
code `0x80060892`), the error envelope gains `meta.alternate_keys` showing each key,
its attributes, and the colliding `payload_values` from the submitted payload.
If the payload also includes the primary-key attribute, a `meta.primary_id_hint`
is added (the same error code fires for primary-key collisions too).
`payload_values` covers plain scalar fields only — lookup bindings
(`field@odata.bind`) are not matched.

## Picklist / option set values (critical before writing a record)

A record write with a bad option-set value is rejected by the server — **look the
values up first.**

Works for Picklist, State (`statecode`), and Status (`statuscode`) attributes:

```bash
crm --json metadata picklist account industrycode    # Picklist
crm --json metadata picklist account statecode       # State
crm --json metadata picklist account statuscode      # Status
# data: raw {"OptionSet": {"Options": [{"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Active"}}}, ...]}}
# meta.options: flattened [{"value": 1, "label": "Active"}, ...] — same for `metadata get-optionset <name>`
```

`meta.options` (JSON mode only) flattens the nested labels to `[{value, label}]` so
you need not dig through `Label.UserLocalizedLabel.Label`; raw `data` is unchanged.
Unsupported types (Boolean, String, etc.) return `ok: false` with a clear error.

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

**Gotcha — logical name, not entity-set name:** `metadata describe` takes the singular
logical name (e.g. `account`), NOT the entity-set name (`accounts`) or a pluralized
form (`webresources`). Passing a set name returns a 404 with `meta.did_you_mean`
suggesting the correct logical name.

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
- A datetime column's format and `DateTimeBehavior` are NOT captured (re-created with
  the server default format and `UserLocal` behavior).
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

## Datetime column behavior gotchas (`--behavior`)

`DateTimeBehavior` controls whether a datetime column stores time-zone-offset data
(`UserLocal`), is treated as a date with no time component (`DateOnly`), or stores
absolute UTC with no conversion (`TimeZoneIndependent`). The value is set on create
and **cannot be changed afterward** — plan before you create.

Two non-obvious coupling rules:

1. **`DateOnly` behavior auto-sets the format.** When `--behavior DateOnly` is given
   and `--format` is omitted, the CLI auto-defaults `--format` to `DateOnly`. Passing
   `--behavior DateOnly --format DateAndTime` explicitly is a server validation error.
2. **`--behavior` is rejected for non-datetime kinds** (errors before any HTTP call).

Omitting `--behavior` leaves the column at the server default (`UserLocal`).

## Auto-number string columns (`--auto-number-format`)

`metadata add-attribute --kind string --auto-number-format "<pattern>"` sets
`AutoNumberFormat` so the server generates the value on insert. Patterns use
`{SEQNUM:n}` (zero-padded sequence) and `{RANDSTRING:n}` (random alphanumerics),
e.g. `INV-{SEQNUM:5}`. String-kind only; ignored/invalid for other kinds.

## Rollup and calculated columns (`--type rollup` / `--type calculated`)

`metadata add-attribute` with `--type rollup` or `--type calculated` turns the
typed column (chosen by `--kind`) into a rollup or calculated field by setting
`SourceType` (2 for rollup, 1 for calculated) and `FormulaDefinition` on the
metadata body. `--formula-file <path>` is required; the XAML is sent verbatim.

**Critical gotcha — formula XAML is editor-authored.** The formula XAML must be
produced by the Dynamics 365 formula editor (or extracted from a solution export).
Hand-written XAML is unsupported: the server validates it and rejects invalid XAML
with "FormulaDefinition is not valid Xaml". Use `--dry-run` to preview the
would-be POST body (including `SourceType` + `FormulaDefinition`) before writing.

The base `--kind` still picks the data type; the server enforces which base
types support rollup vs calculated and rejects an unsupported pairing. The CLI
only rejects `--type rollup`/`calculated` on `--kind lookup`/`customer` up front.

## Inspect the server's entity sets

```bash
crm --json service-document
# returns {"value": [{"name": "accounts", "url": "accounts", ...}, ...]}
```
