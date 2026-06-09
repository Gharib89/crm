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
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical --if-exists skip
```
`--if-exists skip` makes re-runs a no-op; the response reports `created`, the metadata id, and `published: true`.

## Create a custom entity

```bash
crm --json metadata create-entity \
  --schema-name cwx_Ticket --display "Support Ticket" --display-collection "Support Tickets" \
  --primary-attr cwx_Name --primary-label "Ticket Title" \
  --ownership UserOwned --has-notes --has-activities --if-exists skip
```
Note the returned `entity_set_name` (plural, e.g. `cwx_tickets`) — that is what `entity`/`query` commands take, not the logical name.

## Add a picklist column bound to a global option set

```bash
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Priority --display "Priority" --optionset-name cwx_priority --if-exists skip
```
`--kind` also accepts `integer` (with `--min`/`--max`), `memo`, `boolean`, `datetime`, etc.

## Create a 1:N relationship (adds a lookup on the N side)

```bash
crm --json metadata create-one-to-many --schema-name cwx_sla_cwx_ticket \
  --referenced-entity cwx_sla --referencing-entity cwx_ticket \
  --lookup-schema cwx_SLA --lookup-display "SLA Policy" --if-exists skip
```
The response reports the `referencing_attribute` (the lookup column) the server generated on the N-side entity.

## Verify a metadata change landed (`--expect`)

A metadata change isn't readable until it's published. The repeatable `--expect ATTR=VALUE` flag on `metadata attribute` turns the read-back into a self-checking verify step — pair it with a create + publish to poll until the definition reflects the change:

```bash
crm metadata add-attribute cwx_ticket --kind string \
    --schema-name cwx_Label --display "Label" --max-length 100 \
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
```
Returns `can_delete` (bool) and `blockers[]`; each blocker carries `dependent_type`,
`dependent_id`, `dependent_parent_id`, `required_type`, and `dependency_type`. `--for delete` (default) shows
what would block the deletion (`RetrieveDependenciesForDelete`). `--for dependents`
shows what currently depends on the target (`RetrieveDependentComponents`); in that
mode `can_delete` reflects whether anything depends on the target, not a strict
delete-safety check. Read-only — no changes are made.

## Delete a custom column

```bash
crm --json metadata delete-attribute cwx_ticket cwx_priority --yes
```
Pre-flight refuses managed, non-custom, primary (id/name), and sub-attribute targets before any DELETE. Pass `--solution` to scope the delete to a solution. The server rejects with a 4xx if the column is still referenced (forms, views, workflows) — remove those dependencies first. Destructive: needs `--yes` (or an interactive confirmation). Add `--check-dependencies` (with `--dry-run` for a non-destructive preview) to fold blockers into the result:

```bash
crm --json --dry-run metadata delete-attribute cwx_ticket cwx_priority --yes --check-dependencies
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
crm --json metadata delete-relationship cwx_sla_cwx_ticket --yes
```
Works for both 1:N and N:N. Refuses managed and non-custom relationships client-side; the server enforces remaining-dependency checks and returns a 4xx on conflict. Pass `--solution` to scope the delete. Destructive: needs `--yes` (or an interactive confirmation). Pass `--check-dependencies` (optionally with `--dry-run`) to preview blocking dependencies inline before the delete.

## Clone an entity

Duplicate a custom entity under a new schema name. The bare clone copies the
entity, its custom attributes (including lookup columns, which are recreated
pointing at the same parent tables), and the global option sets it references
(by name — not duplicated). Forms, views, workflows, and charts are opt-in.

```bash
# skeleton only (entity + attributes + lookups + reused option sets)
crm metadata clone-entity new_project cwx_TicketClone --display "Ticket Clone"

# everything cloneable over the API (forms, views, workflows, charts)
crm metadata clone-entity new_project cwx_TicketClone --with-all --solution MySolution
```

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
- `-o / --output FILE` — write the bare spec as YAML to FILE. The file is directly
  consumable by `crm apply -f <file>`. Without `-o` the spec is emitted under the
  standard JSON envelope (useful for piping or `--json` capture).

**What is captured:**

- Entity: `schema_name`, `display_name`, `display_collection_name`, `ownership`.
- Primary name attribute: `schema_name` + `label` (represented as `primary_attr`).
- Custom, apply-creatable attributes (14 kinds: `string`, `memo`, `integer`,
  `bigint`, `decimal`, `double`, `money`, `boolean`, `datetime`, `picklist`,
  `multiselect`, `lookup`, `image`, `file`). Each attribute is deep-read to capture
  `MaxLength`, `FormatName`, `Precision`, `RequiredLevel`, and option-set options.
  Picklists/multiselects bound to a global option set emit `optionset_name`; the
  referenced global option set is captured as a top-level `optionsets` entry.
  System attributes (Owner, State, Status, Uniqueidentifier, …) are skipped.
- Relationships (with `--with-relationships`): custom 1:N relationships.
- Views (with `--with-views`): public saved queries with parseable column layouts.
- Publisher and solution are **not** emitted — supply them via `crm apply --solution`
  or by editing the YAML before applying.

**Fidelity note:** these attribute properties round-trip through `apply` —
`max_length`, `required`, option-set options, lookup `target_entity`, `precision`
(decimal/double/money), and string `format_name` (`Email` / `Phone` / `Url` /
`TextArea` / etc.). Caveats:

- A string column whose live format is `Json` or `RichText` (formats `apply` cannot
  create) is re-created as plain `Text`.
- A datetime column's format is **not** captured; it is re-created with the default
  format.
- A polymorphic (multi-target) lookup is exported with its first target only and
  re-created as a single-target lookup (`apply` creates single-target lookups).
- Relationship `cascade` and `associated_menu` configuration are captured but not
  yet acted on by `apply` (`create_one_to_many` does not accept them) — the
  relationship is re-created with default cascade/menu behaviour.

`apply` ignores unknown keys, so the spec file remains apply-consumable throughout.
Attribute types that `apply` cannot create (Owner, State, Status, and other system
kinds) are silently skipped.

**Fidelity warnings.** `export-spec` reports every custom column it cannot
represent in the output spec — for example, a picklist whose metadata cast is
permission-limited, or a lookup with no readable target entity. When running with
`--json`, dropped columns and the reason are collected in `meta.warnings` so
nothing is silently lost.
