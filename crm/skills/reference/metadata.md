# Metadata — schema introspection, picklists, dependencies

Read schema, resolve option-set values before writing, preview deletes, and
round-trip entities to specs. Group: `metadata` (plus top-level `service-document`).
Writing schema of a specific kind (datetime behavior, auto-number, rollup/calculated,
status/state, field mappings, hierarchical, virtual)? See `reference/metadata-writes.md`.
Flags/choices: `crm metadata --help`.
Export an entity as a spec, clone an entity, incremental sync (`metadata
changes`): `reference/metadata-export.md`.

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

## Lean / wide metadata sweeps (server-side projection)

`metadata entities` / `metadata attributes` fetch a fixed field set, and `--fields`
trims **client-side only** — it never shrinks the wire payload. For wide sweeps
where payload or latency matters (all ~1000 tables, attributes across N entities),
go through `query odata` against `EntityDefinitions`: its `--select` / `--filter` /
`--expand` are true server-side OData options.

```bash
# All tables, two scalars — small wire payload
# (EntityDefinitions rejects $top server-side; slice client-side if needed)
crm --json query odata EntityDefinitions --select LogicalName,MetadataId

# Attributes for N entities in ONE GET — nested $select inside --expand
crm --json query odata EntityDefinitions --select LogicalName \
    --filter "LogicalName eq 'account' or LogicalName eq 'contact'" \
    --expand 'Attributes($select=LogicalName,MetadataId,AttributeType)'

# Address an entity by MetadataId (GUID unquoted in the path)
crm --json query odata "EntityDefinitions(<metadata-id-guid>)" \
    --select LogicalName,EntitySetName
```

A single GET also works under read-only profiles, which refuse `$batch` — prefer
this shape over batching per-entity `Attributes` GETs. The rows are **raw OData
attribute metadata**: none of `metadata attributes`' normalization applies (e.g.
`RequiredLevel` stays a nested `{"Value": ...}` object here). For change-driven
sweeps ("which columns changed since the last run?") prefer
`metadata changes --entity <a> --entity <b> --attributes` — one call, N entities,
delta cursor (see `reference/metadata-export.md`).

## Alternate keys (`metadata keys`)

```bash
crm --json metadata keys account
```

Returns `data: [{logical_name, schema_name, key_attributes, index_status}]`.
Empty `data: []` means no alternate keys are defined — not an error.
`index_status` values: `Active`, `Pending`, `Failed`, `InProgress`.

Create/drop the key with `metadata create-key <entity>` /
`metadata delete-key <entity> <key>` (`create-key` requires `--solution`; the hard
`delete-key` does not — see `reference/customization-lifecycle.md`). A freshly created key's index builds
asynchronously (`index_status` `Pending`), and `entity upsert --key` /
`data import --mode upsert --key` 404 against it until it reaches `Active` — poll
`metadata keys` to confirm before upserting (see `reference/records-writes.md` /
`reference/bulk.md`).

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
# discover which global option sets exist: crm --json metadata list-optionsets
```

`meta.options` (JSON mode only) flattens the nested labels to `[{value, label}]` so
you need not dig through `Label.UserLocalizedLabel.Label`; raw `data` is unchanged.
Unsupported types (Boolean, String, etc.) return `ok: false` with a clear error.

## Write-readiness brief — one call before writing a record

```bash
crm --json metadata describe new_project
# data: { entity_set_name, primary_id, primary_name, writable_attributes: [
#   { logical_name, attribute_type, required_level,
#     # lookups:                bind_key:"new_AccountId@odata.bind" (single-target only), targets:[{logical,set_name,bind_key}]
#     # picklist/state/status:  options:[{value,label}]
#     # global-bound picklist:  + global_optionset_id (GUID) } ] }
```

One read-only call that consolidates everything needed to build a valid create/update
payload: the entity set name, primary id/name, every writable column with its required
level, lookup `@odata.bind` keys + resolvable targets, and inline option values.
**Prefer this over chaining `attributes` + `picklist` + `relationships` by hand** —
it hands you the exact `bind_key` and `global_optionset_id` you'd otherwise assemble
manually (see the `@odata.bind` notes in `reference/records-writes.md`).

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

## Preview dependencies before deleting a metadata component

```bash
# What would block deleting an entity
crm --json metadata dependencies contoso_ticket

# What would block deleting a column (dotted entity.attribute)
crm --json metadata dependencies contoso_ticket.contoso_priority --kind attribute

# What depends on a global option set
crm --json metadata dependencies contoso_status --kind optionset --for dependents

# What components the target itself depends on (reverse direction)
crm --json metadata dependencies contoso_ticket --kind entity --for required
```

Returns `{can_delete, blockers[], metadata_id, component_type, kind, for}`; each
blocker carries `dependent_type`, `dependent_id`, `dependent_parent_id`,
`required_type`, `dependency_type`. `--for delete` (default) uses
`RetrieveDependenciesForDelete`; `--for dependents` uses `RetrieveDependentComponents`
(what depends on the target); `--for required` uses `RetrieveRequiredComponents`
(what the target depends on — the reverse of `dependents`). Same output shape for
all three modes. Read-only. To fold dependency info into a delete result non-destructively:

```bash
crm --json --dry-run metadata delete-attribute contoso_ticket contoso_priority --solution ContosoCore --yes --check-dependencies
```

`--check-dependencies` is available on `delete-entity`, `delete-attribute`,
`delete-relationship`, and `delete-optionset`.

## Verify a metadata change landed (`--expect`)

A metadata create/publish can take a moment to propagate. Poll until the definition
reflects the change, then retry if it hasn't:

```bash
crm metadata add-attribute new_widget --kind string \
    --schema-name new_Label --display Label --max-length 100 --solution ContosoCore \
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

## Relationship eligibility (`metadata can-relate`)

Run this **before** `create-one-to-many` or `create-many-to-many` to confirm
both sides are eligible and to discover legal partners — avoids a server-side
fault if an entity is ineligible for the chosen role.

```bash
# Eligibility check — can <entity> play this role?
crm --json metadata can-relate <entity> --as referenced|referencing|many-to-many

# Partner discovery — which tables are legal partners?
crm --json metadata can-relate <entity> --as referenced|referencing|many-to-many \
    --valid-partners
```

**JSON — eligibility check** (`data` only, no `--valid-partners`):

```json
{"entity": "account", "as": "referenced", "eligible": true}
```

**JSON — partner list** (`--valid-partners`):

```json
{"entity": "account", "as": "referenced", "valid_partners": ["contact", ...], "count": 42}
```

**Gotcha — N:N partner list is org-global, not entity-scoped.** When `--as
many-to-many --valid-partners` is used, the underlying `GetValidManyToMany`
action takes no entity argument and returns every N:N-capable table in the org.
The eligibility check (`--as many-to-many` without `--valid-partners`) is still
entity-scoped via `CanManyToMany`.

## Inspect the server's entity sets

```bash
crm --json service-document
# returns {"value": [{"name": "accounts", "url": "accounts", ...}, ...]}
```
