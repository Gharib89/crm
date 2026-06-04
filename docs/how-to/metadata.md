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

## Delete a custom column

```bash
crm --json metadata delete-attribute cwx_ticket cwx_priority --yes
```
Pre-flight refuses managed, non-custom, primary (id/name), and sub-attribute targets before any DELETE. Pass `--solution` to scope the delete to a solution. The server rejects with a 4xx if the column is still referenced (forms, views, workflows) — remove those dependencies first. Destructive: needs `--yes` (or an interactive confirmation).

## Delete a custom relationship

```bash
crm --json metadata delete-relationship cwx_sla_cwx_ticket --yes
```
Works for both 1:N and N:N. Refuses managed and non-custom relationships client-side; the server enforces remaining-dependency checks and returns a 4xx on conflict. Pass `--solution` to scope the delete. Destructive: needs `--yes` (or an interactive confirmation).
