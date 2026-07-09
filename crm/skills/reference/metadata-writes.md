# Metadata writes — column-kind, option-model, relationship & virtual-table gotchas

Non-obvious rules for *writing* schema of specific kinds: datetime behavior,
auto-number, rollup/calculated columns, status/state option models, field
mappings, hierarchical relationships, and virtual tables. The verbs live under
group `metadata`; browse/introspect the schema first via `reference/metadata.md`.
Flags/choices: `crm metadata --help`.

Integer and bigint `--min` / `--max` bounds are whole-number fields on both
`metadata add-attribute` and `metadata update-attribute`; fractional input is
rejected before the request is sent.

## Auditing (`--audit` / `--no-audit`)

`--audit`/`--no-audit` sets the `IsAuditEnabled` managed property on a table or
column, uniformly across `create-entity`, `add-attribute`, `update-entity`, and
`update-attribute`. **Gotcha:** this per-table/column flag is inert until
auditing is turned on at the **organization** level — set it and the value is
stored, but no audit records are written until org auditing is enabled (a
one-time environment step in PPAC, not a crm verb). Omit the flag to leave the
value unchanged.

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
e.g. `INV-{SEQNUM:5}`. String-kind only — passing it with any other `--kind`
is rejected client-side before any HTTP call (same pattern as `--behavior`
above).

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

## Status/state option model writes

`metadata status-add` and `metadata state-relabel` modify the `statuscode` /
`statecode` option sets on an entity. Two non-obvious points:

- **Look up the statecode integer first.** `--state` (on `status-add`) and `--value`
  (on `state-relabel`) take the raw integer, not a label. Confirm the integer before
  writing:

  ```bash
  crm --json metadata picklist <entity> statecode
  # meta.options: [{value: 0, label: "Active"}, {value: 1, label: "Inactive"}]
  ```

- **`--merge-labels` on `state-relabel`.** Without it the server replaces all
  language labels, wiping translated labels you haven't touched. Pass `--merge-labels`
  when the org has more than one language installed to preserve non-default-language
  labels.

- **Status transitions are app-authored only.** `StatusOptionMetadata.TransitionData`
  and `EnforceStateTransitions` cannot be written over the Dataverse Web API. A
  PUT to the attribute definition returns 204 but silently drops option-level data;
  no Web API action accepts `TransitionData`; `EnforceStateTransitions` is read-only
  over the API. There is no CLI verb for this — use the Power Apps designer or
  solution XML.

## Field mappings (`metadata create-mapping`)

Field mappings copy field values from a parent record onto a child created in its
context. The direction rule and the `--auto` destructive gotcha are the two things
`--help` cannot tell you:

**Direction is fixed — parent entity is always the source.** The 1:N relationship's
`ReferencedEntity` (the "1"/parent side) is the map *source*; the `ReferencingEntity`
(the "N"/child side) is the *target*. You cannot flip this. Pass the attribute logical
names accordingly: `--from <parent-attr> --to <child-attr>`.

**Type/length compatibility.** The source and target attributes must be the same type.
For string-type attributes, the target's `MaxLength` must be at least as large as the
source's `MaxLength` — the server rejects a narrower target.

**`--auto` OVERWRITES existing maps.** `AutoMapEntity` replaces all `attributemap`
rows for the entity pair in one call. Any maps you created manually are gone. Use it
for initial bulk setup; for additive changes use `--from`/`--to` on individual calls.

**JSON shape — single mapping:**

```json
{
  "created": true,
  "relationship": "<schema-name>",
  "source_entity": "<referenced entity>",
  "target_entity": "<referencing entity>",
  "source_attribute": "<attr>",
  "target_attribute": "<attr>",
  "entity_map_id": "<guid>",
  "attribute_map_id": "<guid>",
  "solution": "<name or null>"
}
```

**JSON shape — `--auto`:**

```json
{
  "auto_mapped": true,
  "relationship": "<schema-name>",
  "source_entity": "<referenced entity>",
  "target_entity": "<referencing entity>",
  "entity_map_id": "<guid>",
  "solution": "<name or null>"
}
```

## Hierarchical relationships

`--hierarchical` on `create-one-to-many` (flag, default off) or
`--hierarchical / --no-hierarchical` on `update-relationship` (tri-state,
default unset — leaves the existing value alone) sets `IsHierarchical` on a
1:N relationship.

- **Self-referencing required.** The referenced and referencing entity must be
  the same table — passing `--hierarchical` on a cross-entity 1:N is not
  rejected client-side but will fault on the server.
- **One per entity.** Only one hierarchical relationship may be active per
  entity at a time; the server rejects a second.
- **1:N only.** `--hierarchical / --no-hierarchical` is rejected client-side
  when passed to `update-relationship` with an N:N schema name.

## Virtual (external-data-backed) tables

Set `--external-name`, `--external-collection-name`, and `--data-provider` on
`metadata create-entity` to create a virtual table. `--data-source` is
optional. All three of `--external-name`, `--external-collection-name`, and
`--data-provider` are required together — supplying only some of them is a
client-side usage error.

**Workflow — create prerequisites first, entity second:**

1. Find the data-provider record GUID:
   `crm --json query odata entitydataproviders --select entitydataproviderid,name`
2. Optionally find a data-source GUID:
   `crm --json query odata entitydatasources --select entitydatasourceid,name`
3. Run `metadata create-entity` with `--data-provider` (and optionally
   `--data-source`) plus the two `--external-*` flags.

**Caveat — read-only on v9.1.** On-premises v9.1 virtual tables are
**read-only**. Any create/update/delete call against a virtual table returns a
server fault. On Dataverse online, write support depends on the data provider.
