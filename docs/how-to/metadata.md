# How-to: metadata

Recipes for schema work, taken from the CRMWorx build (§2). See the
[CLI reference](../reference/cli.md) for every flag.

## Describe an entity before writing to it

```bash
crm --json metadata describe cwx_ticket
```
One read-only call returns everything needed to build a valid create/update payload:

```json
{
  "entity_set_name": "cwx_tickets",
  "primary_id": "cwx_ticketid",
  "primary_name": "cwx_name",
  "writable_attributes": [
    {"logical_name": "cwx_name", "attribute_type": "String", "required_level": "ApplicationRequired"},
    {"logical_name": "cwx_slaid", "attribute_type": "Lookup", "required_level": "None",
     "bind_key": "cwx_SLA@odata.bind", "targets": [{"logical": "cwx_sla", "set_name": "cwx_slas"}]},
    {"logical_name": "cwx_priority", "attribute_type": "Picklist", "required_level": "None",
     "options": [{"value": 1, "label": "Low"}, {"value": 2, "label": "High"}],
     "global_optionset_id": "8e9f…"}
  ]
}
```
- **`bind_key`** is the `<Nav>@odata.bind` key for a lookup — use it directly in an `entity create` payload with a value of `/<set_name>(<guid>)`.
- **`targets[].set_name`** is the entity set the lookup points at, so the bind value is ready to assemble.
- **`options`** gives the inline `{value, label}` choices for picklist / state / status columns.
- **`global_optionset_id`** appears only when a picklist is bound to a *global* option set; on-prem 9.1 needs that GUID to bind on create.

Pure GETs — gated so only the attribute kinds the entity actually uses cost a round-trip.

## List alternate keys on an entity

```bash
crm --json metadata keys account
```

Returns `data: [{logical_name, schema_name, key_attributes, index_status}]` for every
alternate key defined on the entity.  An entity with no keys returns `data: []` — that
is not an error.

```json
{
  "ok": true,
  "data": [
    {
      "logical_name": "account_code_ak",
      "schema_name": "Account_Code_AK",
      "key_attributes": ["accountnumber"],
      "index_status": "Active"
    }
  ],
  "meta": {"count": 1}
}
```

`index_status` mirrors `EntityKeyIndexStatus` from the Dataverse Web API —
typical values are `Active`, `Pending`, `Failed`, `InProgress`.

## Create an alternate key

```bash
crm --json metadata create-key account --name new_AccountCode \
  --key-attributes accountnumber --solution cwx_crmworx --if-exists skip

# composite key (two or more attributes)
crm --json metadata create-key cwx_sla --name cwx_TierRegion \
  --key-attributes cwx_tier,cwx_region --solution cwx_crmworx
```

`--key-attributes` is a comma-separated list of attribute **logical** names.
`--solution` is required — a component created without an explicit target
solution would otherwise land only in the system Default Solution; pass
`--solution Default` for a deliberate Default-Solution-only write. The
server builds the supporting index asynchronously, so a freshly created key starts
with `index_status` `Pending` — poll `metadata keys <entity>` (or
`entity upsert --key` returns 404) until it reaches `Active`. `--if-exists skip`
makes re-runs a no-op. This is the key that `entity upsert --key` and
`data import --mode upsert --key` match records on.

## Delete an alternate key

```bash
crm --json metadata delete-key account new_accountcode --solution cwx_crmworx --yes
```

Addresses the key by its logical name (lower-case; the schema name also works).
`--solution` is optional here — a hard delete removes the component globally, so the header can't scope or orphan it. Destructive: needs `--yes` (or an
interactive confirmation).

## Read option set values (flattened)

```bash
crm --json metadata picklist account industrycode
crm --json metadata get-optionset cwx_priority
```
Both commands return the raw Dataverse metadata under `data` *and* a flattened
`meta.options = [{value, label}]` convenience list (JSON mode only), so you need not
walk `Label.UserLocalizedLabel.Label` by hand. `picklist` reads the local `OptionSet`,
falling back to `GlobalOptionSet`; `get-optionset` reads the global set's root `Options`.
A *boolean* attribute has no `Options` array (it carries `TrueOption` / `FalseOption`),
so its `meta.options` is empty — read those raw fields instead.

## Create a global option set (idempotent)

```bash
crm --json metadata create-optionset --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical \
  --solution cwx_crmworx --if-exists skip
```
`--solution` is required on every metadata write verb (`create-*`, `update-*`,
`delete-*`, `add-attribute`) — a component created without an explicit target
solution would otherwise land only in the system Default Solution; pass
`--solution Default` for a deliberate Default-Solution-only write.
`--if-exists skip` makes re-runs a no-op; the response reports `created`, the metadata id, and `published: true`.

## List entities filtered by managed/custom

```bash
# custom (unmanaged) entities only
crm --json metadata entities --custom-only --top 20

# managed entities only (solution-installed)
crm --json metadata entities --managed-only --top 20

# arbitrary OData $filter passthrough (combined with the above via AND)
crm --json metadata entities --filter "IsActivity eq true"
```

`--managed-only` adds `IsManaged eq true`; `--filter` appends a raw OData expression.
The human table includes an `IsManaged` column. Filters are rejected with the
entity-definition cache (`--cache-metadata` / `--refresh-metadata`), which stores only
logical/set names.

## Create a custom entity

```bash
crm --json metadata create-entity \
  --schema-name cwx_Ticket --display "Support Ticket" --display-collection "Support Tickets" \
  --primary-attr cwx_Name --primary-label "Ticket Title" \
  --ownership UserOwned --has-notes --has-activities \
  --solution cwx_crmworx --if-exists skip
```
Note the returned `entity_set_name` (plural, e.g. `cwx_tickets`) — that is what `entity`/`query` commands take, not the logical name.

## Create a virtual (external-data-backed) table

A virtual table maps to data held in an external store. Rows are never
persisted in Dataverse — reads are delegated to the registered data provider
at query time.

**Prerequisites — configure first, create second.**

1. Register a data provider record (the plugin assembly + registration that
   knows how to query your external store) — obtain its GUID from
   `crm query odata entitydataproviders` or your provider's documentation.
2. Optionally register a data source record (connection config for the
   provider) and note its GUID.

Only then run `create-entity`:

```bash
crm --json metadata create-entity \
  --schema-name cwx_ExternalProduct \
  --display "External Product" \
  --display-collection "External Products" \
  --data-provider  "<data-provider-guid>" \
  --external-name "products" \
  --external-collection-name "products" \
  --data-source "<data-source-guid>" \  # optional
  --solution cwx_crmworx
```

`--external-name`, `--external-collection-name`, and `--data-provider` are
required together; `--data-source` is optional. Setting any of these flags
creates a virtual table — omitting all of them creates an ordinary table.

**Caveat — read-only on v9.1.** On-premises v9.1 virtual tables are
**read-only**: create/update/delete operations are not supported and the
server returns a fault. On Dataverse online the data provider determines
write support.

## Add a picklist column bound to a global option set

```bash
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Priority --display "Priority" --optionset-name cwx_priority \
  --solution cwx_crmworx --if-exists skip
```
`--kind` also accepts `integer` (with `--min`/`--max`), `memo`, `boolean`, `datetime`, etc.
For `--kind string`/`memo`, `--max-length` is optional — omit it to default to 100 / 2000.

## Add a datetime column with specific behavior

```bash
# Default — DateAndTime format; behavior omitted, so the server applies UserLocal
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_DueDate --display "Due Date" --solution cwx_crmworx

# DateOnly behavior (date with no time component)
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_DueDate --display "Due Date" --behavior DateOnly --solution cwx_crmworx

# TimeZoneIndependent — stored and displayed without conversion
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_ScheduledAt --display "Scheduled At" \
  --behavior TimeZoneIndependent --format DateAndTime --solution cwx_crmworx
```

`--behavior` accepts `UserLocal`, `DateOnly`, or `TimeZoneIndependent` and sets the
`DateTimeBehavior` property on the column. When omitted the server defaults to `UserLocal`.

**DateOnly↔format coupling.** `DateOnly` behavior is incompatible with the `DateAndTime`
format. When `--behavior DateOnly` is given and `--format` is omitted, the format
auto-defaults to `DateOnly`. If you pass both `--behavior DateOnly --format DateAndTime`
explicitly the server will reject the request with a validation error.

**Behavior is immutable after create.** `DateTimeBehavior` cannot be changed after the
column is created — get it right on create. `--behavior` is only valid for
`--kind datetime`; using it with any other kind is rejected with an error.

## Add an auto-number string column

```bash
crm --json metadata add-attribute cwx_ticket --kind string \
  --schema-name cwx_TicketNumber --display "Ticket Number" \
  --auto-number-format "TKT-{SEQNUM:5}" --solution cwx_crmworx
```

`--auto-number-format` sets `AutoNumberFormat` on a string column so the server
generates values on insert. Placeholders include `{SEQNUM:n}` (zero-padded running
number) and `{RANDSTRING:n}` (random alphanumerics) — e.g. `INV-{SEQNUM:5}` →
`INV-00042`. It is only valid with `--kind string`.

## Check relationship eligibility before creating (can-relate)

`metadata can-relate` is a read-only diagnostic you should run before
`create-one-to-many` or `create-many-to-many` to avoid a server-side fault.

```bash
# Yes/no eligibility: can 'cwx_ticket' be the N-side (referencing) of a 1:N?
crm --json metadata can-relate cwx_ticket --as referencing

# List all tables that can legally be the 1-side (referenced) partner of 'account'
crm --json metadata can-relate account --as referenced --valid-partners
```

**JSON shape — eligibility check** (no `--valid-partners`):
```json
{"ok": true, "data": {"entity": "cwx_ticket", "as": "referencing", "eligible": true}}
```

**JSON shape — partner list** (with `--valid-partners`):
```json
{"ok": true, "data": {"entity": "account", "as": "referenced",
  "valid_partners": ["contact", "opportunity", ...], "count": 42}}
```

**Gotcha — N:N partner list is org-global.** When `--as many-to-many
--valid-partners` is used, the partner list comes from `GetValidManyToMany`,
which takes no entity argument. It returns all N:N-capable tables in the org —
not partners specific to the entity you named. The eligibility check
(`--as many-to-many` without `--valid-partners`) is entity-scoped.

## Create a 1:N relationship (adds a lookup on the N side)

```bash
crm --json metadata create-one-to-many --schema-name cwx_sla_cwx_ticket \
  --referenced-entity cwx_sla --referencing-entity cwx_ticket \
  --lookup-schema cwx_SLA --lookup-display "SLA Policy" \
  --solution cwx_crmworx --if-exists skip
```
The response reports the `referencing_attribute` (the lookup column) the server generated on the N-side entity.

## Create a hierarchical (parent/child) relationship

A hierarchical relationship enables the `Above` / `Under` operators and the
parent/child tree views in D365. Only one hierarchical relationship can be
active per entity at a time, and the referenced and referencing entities must
be the same (a self-referencing 1:N).

```bash
# Make cwx_ticket self-referential with a hierarchy
crm --json metadata create-one-to-many \
  --schema-name cwx_ticket_cwx_ticket_parent \
  --referenced-entity cwx_ticket \
  --referencing-entity cwx_ticket \
  --lookup-schema cwx_ParentTicket \
  --lookup-display "Parent Ticket" \
  --hierarchical --solution cwx_crmworx

# Set (or clear) IsHierarchical on an existing 1:N relationship
crm --json metadata update-relationship cwx_ticket_cwx_ticket_parent --hierarchical --solution cwx_crmworx
crm --json metadata update-relationship cwx_ticket_cwx_ticket_parent --no-hierarchical --solution cwx_crmworx
```

`--hierarchical` is only accepted on 1:N relationships; passing it on an N:N
schema name is rejected client-side with an error.

### Pre-flight a referenced object with `--dry-run`

These name-taking writes point at other server objects: `add-attribute --kind lookup` names a `--target-entity`, `add-attribute --kind picklist/multiselect` names an `--optionset-name`, and `create-one-to-many` names its referenced/referencing entities. Under `--dry-run` the command resolves each and reports it under `data.references[] = {kind, value, _exists}`:

```bash
crm --dry-run --json metadata add-attribute cwx_ticket --kind lookup \
  --schema-name cwx_OwnerId --display "Owner" --target-entity cwx_missing \
  --solution cwx_crmworx
```

A reference that does not resolve keeps the preview non-failing (`ok: true`) and adds a `meta.warnings` advisory naming it — so a dangling target entity or option set surfaces as a pre-flight finding instead of a server 400/404 at write time.

## Add a Customer column (composite account/contact lookup)

A *Customer* column is a single lookup that can point at **either an account or a contact** — the type used by the built-in `customerid`. It can't be made by a plain attribute write or a single-target `--kind lookup`; the server builds it from a dedicated action that creates the lookup plus one 1:N relationship to each of `account` and `contact` in one call.

```bash
crm --json metadata add-attribute cwx_ticket --kind customer \
  --schema-name cwx_CustomerId --display "Customer" --solution cwx_crmworx --if-exists skip
```

The targets are fixed to `account` + `contact`, so `--kind customer` takes no `--target-entity` (and the two relationship schema names are derived as `<entity>_<lookup>_account` / `_contact` — they aren't user-nameable). The result reports `targets: ["account", "contact"]` and the created `relationship_ids`.

## Add a rollup or calculated column

Rollup and calculated fields are typed columns (chosen by `--kind`) whose
values are derived from a formula or an aggregation expression. The CLI creates
the attribute *shell* and sets `SourceType` on it; the formula body itself
(`FormulaDefinition`) is a XAML blob that must be supplied via `--formula-file`.

**Critical caveat — formula XAML is editor-authored.** The formula XAML is
officially authored by the Dynamics 365 formula editor. Hand-authoring valid XAML
headlessly works in principle but is unsupported: the server validates the XAML
and rejects an invalid body with "FormulaDefinition is not valid Xaml". Capture
the XAML from the editor (export the solution and inspect the attribute XML, or
use an SDK tool) rather than writing it by hand.

```bash
# Calculated integer column — SourceType=1
crm --json metadata add-attribute account \
  --kind integer --schema-name new_Total --display "Total" \
  --type calculated --formula-file calculated.xaml --solution cwx_crmworx

# Rollup money column — SourceType=2
crm --json metadata add-attribute account \
  --kind money --schema-name new_TotalRevenue --display "Total Revenue" \
  --precision 2 --type rollup --formula-file rollup.xaml --solution cwx_crmworx

# Dry-run previews the would-be POST body (SourceType + FormulaDefinition) without writing
crm --dry-run --json metadata add-attribute account \
  --kind integer --schema-name new_Total --display "Total" \
  --type rollup --formula-file rollup.xaml --solution cwx_crmworx
```

`--kind` picks the data type; `--type` layers rollup or calculated on top. The
server enforces which base types each source supports (e.g. a rollup must be a
numeric or datetime column) and rejects an unsupported pairing — the CLI only
rejects `--type rollup`/`calculated` on `--kind lookup`/`customer` up front. Both
honour `--json`, `--dry-run`, and `--solution`, and work on on-prem (NTLM v9.x)
and Dataverse online (OAuth). `--type simple` is the default and rejects
`--formula-file`; `--type rollup` / `calculated` require it.

## Verify a metadata change landed (`--expect`)

A metadata change isn't readable until it's published. The repeatable `--expect ATTR=VALUE` flag on `metadata attribute` turns the read-back into a self-checking verify step — pair it with a create + publish to poll until the definition reflects the change:

```bash
crm metadata add-attribute cwx_ticket --kind string \
    --schema-name cwx_Label --display "Label" --max-length 100 --solution cwx_crmworx \
  && crm solution publish-all \
  && crm --json metadata attribute cwx_ticket cwx_label --expect AttributeType=String \
  || echo "attribute not ready yet — retry"
```

Each pair passes only if `str(record[ATTR]) == VALUE`; multiple `--expect` flags are AND-gated. The first mismatch exits **1** with `{"ok": false, "error": "Expectation failed: ...", "meta": {"attr": ..., "expected": ..., "actual": ...}}`, so a shell `||` branch (or an agent loop) can retry until the change propagates. All pairs match → normal `ok:true`, exit 0. A malformed `--expect` (no `=`) is a usage error (exit 2) raised before any HTTP. Attribute logical names are lowercase (`cwx_label`); the schema name is PascalCase (`cwx_Label`).

## Preview dependencies before deleting

```bash
crm --json metadata dependencies cwx_ticket
crm --json metadata dependencies cwx_ticket.cwx_priority --kind attribute
crm --json metadata dependencies cwx_status --kind optionset
crm --json metadata dependencies cwx_sla_cwx_ticket --kind relationship --for dependents
crm --json metadata dependencies cwx_ticket --kind entity --for required
```
Returns `can_delete` (bool) and `blockers[]`; each blocker carries `dependent_type`,
`dependent_id`, `dependent_parent_id`, `required_type`, and `dependency_type`. `--for delete` (default) shows
what would block the deletion (`RetrieveDependenciesForDelete`). `--for dependents`
shows what currently depends on the target (`RetrieveDependentComponents`); in that
mode `can_delete` reflects whether anything depends on the target, not a strict
delete-safety check. `--for required` shows the components the target itself depends
on (`RetrieveRequiredComponents`) — the reverse direction of `--for dependents`.
Read-only — no changes are made.

## Delete a custom column

```bash
crm --json metadata delete-attribute cwx_ticket cwx_priority --solution cwx_crmworx --yes
```
Pre-flight refuses managed, non-custom, primary (id/name), and sub-attribute targets before any DELETE. `--solution` is optional here — a hard delete removes the column globally, so the header can't scope or orphan it. The server rejects with a 4xx if the column is still referenced (forms, views, workflows) — remove those dependencies first. Destructive: needs `--yes` (or an interactive confirmation). Add `--check-dependencies` (with `--dry-run` for a non-destructive preview) to fold blockers into the result:

```bash
crm --json --dry-run metadata delete-attribute cwx_ticket cwx_priority --solution cwx_crmworx --yes --check-dependencies
```

## Speed up repeated calls with the entity-definition cache

By default `crm metadata entities` fetches entity definitions live on every call.
Pass `--cache-metadata` (or set `CRM_CACHE_METADATA=1`) to read from a persistent
on-disk cache instead, which is useful for agent loops that call the command
repeatedly:

```bash
crm --json --cache-metadata metadata entities
# meta.cache: "hit"   — served from disk
# meta.cache: "miss"  — cache empty or expired; fetched live and saved
```

### Force a refresh

```bash
crm --json --refresh-metadata metadata entities
# meta.cache: "refreshed" — always fetches live and overwrites the cache
```

`--refresh-metadata` is a one-shot flag with no env-var equivalent. It activates
the cache on its own (you don't also need `--cache-metadata`) and always performs
a live fetch, overwriting the cached copy.

### Cache-mode limitations

In cache mode the command returns **only the 2-field rows** (LogicalName /
EntitySetName) because that is all the cache stores. The full 5-field listing is
unchanged when `--cache-metadata` is absent.

`--custom-only` is **not** supported with `--cache-metadata` (the cache does not
store the custom flag) and exits 2 with a usage error. `--top` works as a
client-side slice.

### Cache file location and TTL

The cache file lives at:

```
<CRM_HOME or ~/.crm>/cache/<profile-name>/entitydefs.json
```

It stores the `{logical, set_name}` list plus the source `url`, `api_version`, and
`cached_at` timestamp. A url/api_version mismatch is treated as a miss. A ~15-minute
TTL backstop forces a refresh even when the file is present. Cache misses and read
errors degrade gracefully — the command falls back to a live fetch.

### Automatic write-invalidation

Any successful metadata write (entity/attribute/optionset/relationship
create/update/delete, and publish-all/publish-xml) deletes the profile's cache file
automatically, so a stale cache cannot outlive a schema change.

### Clear the cache manually

```bash
crm --json metadata cache-clear
# {"ok": true, "data": {"cleared": true}}
```

Returns `{"cleared": false}` if no cache file existed for the active profile.

### Scope

The cache stores read-only schema (entity logical names and set names) only.
Records and secrets are never cached. When the REPL is launched with
`--cache-metadata`, its entity-name tab completion is served from the same
on-disk cache.

## Delete a custom relationship

```bash
crm --json metadata delete-relationship cwx_sla_cwx_ticket --solution cwx_crmworx --yes
```
Works for both 1:N and N:N. Refuses managed and non-custom relationships client-side; the server enforces remaining-dependency checks and returns a 4xx on conflict. `--solution` is optional here — a hard delete removes the component globally, so the header can't scope or orphan it. Destructive: needs `--yes` (or an interactive confirmation). Pass `--check-dependencies` (optionally with `--dry-run`) to preview blocking dependencies inline before the delete.

## Clone an entity

Duplicate a custom entity under a new schema name. The bare clone copies the
entity, its custom attributes (including lookup columns, which are recreated
pointing at the same parent tables), and the global option sets it references
(by name — not duplicated). Forms, views, workflows, and charts are opt-in.

```bash
# skeleton only (entity + attributes + lookups + reused option sets)
crm metadata clone-entity new_project cwx_TicketClone --display "Ticket Clone" --solution MySolution

# everything cloneable over the API (forms, views, workflows, charts)
crm metadata clone-entity new_project cwx_TicketClone --with-all --solution MySolution
```

`--solution` is required — a component created without an explicit target
solution would otherwise land only in the system Default Solution.

`--with-forms` clones **Main** forms only. `--with-workflows` clones classic
workflows and business rules whose primary entity is the source; actions, BPFs,
dialogs, and modern flows are skipped (reported under `skipped_workflows`), and
because there is no "is custom" filter it copies every matching definition
(type=1), including managed ones. `--with-charts` clones public system charts
(`savedqueryvisualization`); each chart's `datadescription` FetchXML is
retargeted to the clone entity via a whole-token name swap.

**Views and the ObjectTypeCode timing caveat:** A brand-new entity's
ObjectTypeCode (OTC) is sometimes unreadable until after the first apply's
publish step. When this happens, `--with-views` puts views in the *planned*
state rather than applying them immediately; the command surfaces this via a
`views_note` warning. If you see that warning, re-run the clone with
`--with-views` (and without `--with-forms` / `--with-workflows` so it is
idempotent) after the initial publish to land the views.

**Not cloned (Web API limits):**

- **Ribbon** — `RibbonDiffXml` has no Web API write path; it deploys only via
  solution import. The result carries a `ribbon_note` saying so.
- **N:N relationships**, and 1:N relationships where the source is the *parent*
  (referenced) side — cloning those would add lookups to *other* tables.
- **Lookup cascade / associated-menu behavior** — recreated lookups use the
  default cascade behavior, not the source's.
- **Polymorphic / Customer lookups** — only single-target lookups come across.
- On Unified Interface a cloned form may need adding to the model-driven app's
  form list to be user-visible.

## Export a live entity as an apply spec (export-spec → apply round-trip)

`crm metadata export-spec` reads an existing entity over the Web API (pure GETs)
and emits the `{"entities": [...]}` desired-state spec consumed by `crm apply -f`.
This lets you capture an existing entity's schema and re-create it in another
environment, or treat it as a starting point for declarative management.

```bash
# Export to a YAML file ready for crm apply -f
crm metadata export-spec new_project \
    --with-views --with-relationships \
    --solution ContosoCore \
    -o project.yaml

# Then apply it (creates the entity and all captured components, idempotent)
crm apply -f project.yaml
```

**Flags:**

- `--with-views` — include the entity's public views (saved queries with non-empty
  column layouts) in the spec. Views with empty column layouts are dropped because
  `apply` requires at least one column per view.
- `--with-relationships` — include the entity's custom 1:N relationships (including
  `CascadeConfiguration` and `AssociatedMenuConfiguration`) in the spec.
- `--solution NAME` — bake a top-level `solution: {unique_name: NAME}` block into
  the spec so it applies directly. `crm apply` requires one; omit `--solution` to
  emit a valid but non-appliable document (add the block by hand, or re-export
  with `--solution`, before running `crm apply -f`).
- `-o / --output FILE` — write the bare spec as YAML to FILE. The file is directly
  consumable by `crm apply -f <file>`. Without `-o` the spec is emitted under the
  standard JSON envelope (useful for piping or `--json` capture).

**What is captured:**

- Entity: `schema_name`, `display_name`, `display_collection_name`, `ownership`,
  `has_notes`, `has_activities`, `primary_attr_max_length`. Fields equal to their
  platform default are omitted.
- Primary name attribute: `schema_name` + `label` (represented as `primary_attr`).
- Custom, apply-creatable attributes (14 kinds: `string`, `memo`, `integer`,
  `bigint`, `decimal`, `double`, `money`, `boolean`, `datetime`, `picklist`,
  `multiselect`, `lookup`, `image`, `file`). Each attribute is deep-read to capture
  `MaxLength`, `FormatName`, `Precision`, `RequiredLevel`, and option-set options.
  Also captured where applicable: `auto_number_format` (string), `min_value` /
  `max_value` (integer/bigint), `behavior_name` (datetime), `max_size_kb` (file).
  Picklists/multiselects bound to a global option set emit `optionset_name`; the
  referenced global option set is captured as a top-level `optionsets` entry.
  Calculated and rollup columns (custom columns with `SourceType` 1/2) are also
  captured: the exported spec includes `source_type` (`"calculated"` or `"rollup"`)
  and `formula_definition` (the live FormulaDefinition XAML), so `apply` can
  re-create them in another environment. If the FormulaDefinition cannot be read,
  the column is exported as a plain simple column and a warning is emitted.
  System attributes (Owner, State, Status, Uniqueidentifier, …) are skipped.
- Relationships (with `--with-relationships`): custom 1:N relationships, including
  flat cascade keys (`cascade_assign`, `cascade_delete`, `cascade_reparent`,
  `cascade_share`, `cascade_unshare`, `cascade_merge`), associated-menu keys
  (`menu_behavior`, `menu_label`, `menu_order`), `is_hierarchical`, and the lookup
  column's `lookup_description`. Keys equal to platform defaults are omitted.
- Views (with `--with-views`): public saved queries with parseable column layouts,
  including `filter_active` and `order_desc` where set.
- A publisher is never emitted — an existing entity does not know its publisher.
  A top-level `solution:` block is emitted only when `--solution <name>` is
  passed to `export-spec`; `crm apply` requires one, so a spec exported without
  it is valid but not appliable until you add a `solution:` block (or re-export
  with `--solution`).

**Fidelity note:** these attribute properties round-trip through `apply` —
`max_length`, `required`, option-set options, lookup `target_entity`, `precision`
(decimal/double/money), string `format_name` (`Email` / `Phone` / `Url` /
`TextArea` / etc.), and calculated/rollup `source_type` + `formula_definition`.
Caveats:

- A string column whose live format is `Json` or `RichText` (formats `apply` cannot
  create) is re-created as plain `Text`.
- A datetime column's display *format* is **not** captured (re-created with the
  server default format); its `DateTimeBehavior` **is** captured as `behavior_name`
  when it differs from the `UserLocal` default.
- A polymorphic (multi-target) lookup is exported with its first target only and
  re-created as a single-target lookup (`apply` creates single-target lookups).

`apply` ignores unknown keys, so the spec file remains apply-consumable throughout.
Attribute types that `apply` cannot create (Owner, State, Status, and other system
kinds) are silently skipped.

**Fidelity warnings.** `export-spec` reports every custom column it cannot
represent in the output spec — for example, a picklist whose metadata cast is
permission-limited, or a lookup with no readable target entity. When running with
`--json`, dropped columns and the reason are collected in `meta.warnings` so
nothing is silently lost.

## Add a statuscode option to a state

```bash
# Add a "Pending" status tied to the Active state (statecode 0)
crm --json metadata status-add cwx_ticket --state 0 --label "Pending" --solution cwx_crmworx --publish

# Supply an explicit numeric value (must be unique; server validates)
crm --json metadata status-add cwx_ticket --state 0 --label "Escalated" --value 100001 --solution cwx_crmworx --publish

# Preview without writing
crm --dry-run --json metadata status-add cwx_ticket --state 0 --label "Pending" --solution cwx_crmworx
```

`--state` is the `statecode` value the new option belongs to (e.g. `0` = Active on most
entities; check `crm --json metadata picklist <entity> statecode` to confirm). When
`--value` is omitted the server assigns the next available value with the publisher prefix.
`--solution` is required. Pass `--publish` to publish immediately; without it the
change is staged and `meta.warnings` will carry a "staged, not published" advisory.

```json
{
  "ok": true,
  "data": {
    "added": true,
    "entity": "cwx_ticket",
    "attribute": "statuscode",
    "state_code": 0,
    "value": 100003,
    "solution": "cwx_crmworx"
  }
}
```

## Relabel a statecode state option

```bash
# Rename the Inactive state (statecode 1) to "Closed"
crm --json metadata state-relabel cwx_ticket --value 1 --label "Closed" --solution cwx_crmworx --publish

# Preserve existing labels in other languages while updating the default language
crm --json metadata state-relabel cwx_ticket --value 1 --label "Closed" \
    --merge-labels --solution cwx_crmworx --publish

# Dry-run preview
crm --dry-run --json metadata state-relabel cwx_ticket --value 1 --label "Closed" --solution cwx_crmworx
```

`--value` is the `statecode` integer to relabel. Typical values are `0` (Active) and `1`
(Inactive), but custom entities may vary — check `crm --json metadata picklist <entity>
statecode` first. `--merge-labels` sets `MergeLabels: true` on the server call, which
preserves the translated label text for languages you are not updating; without it the
server replaces all language labels. `--solution` is required to scope the change.

```json
{
  "ok": true,
  "data": {
    "updated": true,
    "entity": "cwx_ticket",
    "attribute": "statecode",
    "value": 1,
    "solution": "cwx_crmworx"
  }
}
```

## Create a field mapping on a 1:N relationship

Field (attribute) mappings cause Dataverse to copy field values from the parent record
onto a child record when the child is created in the context of the parent (e.g. from a
sub-grid). The mapping direction is fixed by the relationship: the **referenced
(parent/"1") entity is always the source**; the **referencing (child/"N") entity is
always the target**. The target attribute must be the same type as the source and its
maximum length must be at least as large as the source's maximum length.

```bash
# Single mapping: copy accountnumber from account (parent) to cwx_accountref (child)
crm --json metadata create-mapping new_account_cwx_ticket \
    --from accountnumber --to cwx_accountref --solution MyCust

# Bulk-generate the likely mappings for the pair via AutoMapEntity
# WARNING: --auto REPLACES all existing maps for the entity pair (Dataverse semantics)
crm --json metadata create-mapping new_account_cwx_ticket --auto --solution MyCust

# Preview a single mapping without writing
crm --dry-run --json metadata create-mapping new_account_cwx_ticket \
    --from accountnumber --to cwx_accountref --solution MyCust
```

`--auto` calls the `AutoMapEntity` Web API action, which **overwrites** any manually
created maps for the same entity pair — use it as an initial bulk setup, not as an
additive operation. The relationship must be a 1:N (one-to-many) that supports mapping
(an `entitymap` row must exist for the pair).

```json
{
  "ok": true,
  "data": {
    "created": true,
    "relationship": "new_account_cwx_ticket",
    "source_entity": "account",
    "target_entity": "cwx_ticket",
    "source_attribute": "accountnumber",
    "target_attribute": "cwx_accountref",
    "entity_map_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "attribute_map_id": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
    "solution": "MyCust"
  }
}
```

**Note on status-reason transitions.** Custom state-model transitions
(`StatusOptionMetadata.TransitionData` / `EnforceStateTransitions`) cannot be written
over the Dataverse Web API — a PUT to the attribute definition returns 204 but silently
drops option-level data, no Web API action accepts `TransitionData`, and
`EnforceStateTransitions` is read-only over the API. Transitions are app-authored only
(Power Apps designer / solution XML).

## Track metadata changes incrementally (`metadata changes`)

`metadata changes` wraps the Dataverse `RetrieveMetadataChanges` function. The
pattern is: run once without `--since` to capture a baseline stamp, then pass the
stamp back on subsequent runs to receive only the delta.

### Step 1 — baseline snapshot (first run)

```bash
crm --json metadata changes > baseline.json
# Save the stamp for later
STAMP=$(python3 -c "import json,sys; print(json.load(sys.stdin)['data']['server_version_stamp'])" < baseline.json)
```

The response includes every entity visible to the filter at baseline time. The
`server_version_stamp` value is opaque — treat it as a cursor string.

### Step 2 — delta poll (subsequent runs)

```bash
crm --json metadata changes --since "$STAMP"
```

Only entities that changed since the stamp are returned in `data.entities`. The
response carries a new `server_version_stamp` — replace your saved stamp with the
new one before the next poll. `data.deleted_count` is the count of metadata
components deleted since the stamp; the API does not return their details, only the
count.

### Scope to specific tables

```bash
crm --json metadata changes --since "$STAMP" --entity account --entity contact
```

Omitting `--entity` queries every table. **On a baseline call (no `--since`) this is
equivalent to `RetrieveAllEntities` — a heavy call on orgs with many tables.** Always
scope with `--entity` when you only care about a known subset, and reserve the
unfiltered baseline for true full-org sync scenarios.

### Include column definitions

```bash
crm --json metadata changes --since "$STAMP" --entity account --attributes
```

`--attributes` expands each returned entity with its attribute (column) definitions,
so column-level changes are visible. This increases response size and latency
proportionally.

### JSON shape

```json
{
  "ok": true,
  "data": {
    "server_version_stamp": "<opaque string — save and pass as --since next run>",
    "entities": [
      {
        "logical_name": "account",
        "schema_name": "Account",
        "has_changed": true,
        "attributes": [
          {"logical_name": "name", "attribute_type": "String", "has_changed": true}
        ]
      }
    ],
    "count": 1,
    "deleted_count": 0
  },
  "meta": {"count": 1}
}
```

In `--json` mode the stamp and deleted count live in `data`
(`server_version_stamp`, `deleted_count`) — read them there; `meta` only carries
`count`. (In human-table mode they render from `meta` instead.) `attributes` is
only present when `--attributes` is passed. This is a pure read — it runs live
even under `--dry-run` (reads-execute rule).

## List callable actions and functions

```bash
crm --json metadata list-actions
crm --json metadata list-functions
```

Both commands read the CSDL `$metadata` document and return every OData action or
function defined in the org (built-in Dataverse operations and any custom process
actions). They are read-only — no changes are made.

**JSON shape — actions** (`data` is a bare array):

```json
{
  "ok": true,
  "data": [
    {
      "name": "ImportSolution",
      "is_bound": false,
      "return_type": "mscrm.ImportSolutionResponse",
      "parameters": [
        {"name": "CustomizationFile", "type": "Edm.Binary"},
        {"name": "OverwriteUnmanagedCustomizations", "type": "Edm.Boolean"}
      ]
    }
  ],
  "meta": {"count": 1}
}
```

**JSON shape — functions** (same as actions, plus `is_composable`):

```json
{
  "ok": true,
  "data": [
    {
      "name": "WhoAmI",
      "is_bound": false,
      "is_composable": false,
      "return_type": "mscrm.WhoAmIResponse",
      "parameters": []
    }
  ],
  "meta": {"count": 1}
}
```

**Field meanings:**

- **`is_bound`** — `true` when the callable binds to an entity or entity collection
  (its first parameter is typed `mscrm.<entity>` or `Collection(mscrm.<entity>)`).
  Unbound callables are invoked at the service root.
- **`return_type`** — the OData type string from the CSDL `<ReturnType>` element
  (e.g. `"mscrm.WhoAmIResponse"`, `"Collection(mscrm.systemuser)"`), or `null` when
  the callable has no return type.
- **`is_composable`** — functions only. `true` when the function can appear inside an
  OData query expression (composed with `$filter`, `$orderby`, etc.); `false` for the
  majority of functions.

Actions never carry `is_composable` — OData actions are never composable by spec.
