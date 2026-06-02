# How-to: metadata

Recipes for schema work, taken from the CRMWorx build (§2). See the
[CLI reference](../reference/cli.md) for every flag.

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
