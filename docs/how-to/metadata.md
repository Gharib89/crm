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

## Delete a custom relationship

```bash
crm --json metadata delete-relationship cwx_sla_cwx_ticket --yes
```
Works for both 1:N and N:N. Refuses managed and non-custom relationships client-side; the server enforces remaining-dependency checks and returns a 4xx on conflict. Pass `--solution` to scope the delete. Destructive: needs `--yes` (or an interactive confirmation). Pass `--check-dependencies` (optionally with `--dry-run`) to preview blocking dependencies inline before the delete.
