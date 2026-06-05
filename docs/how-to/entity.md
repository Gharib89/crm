# How-to: entity

Record CRUD recipes, taken from the CRMWorx build (§3). See the
[CLI reference](../reference/cli.md) for every flag.

## Create a record

```bash
crm --json entity create cwx_slas --data '{"cwx_name":"Gold 4h/24h","cwx_responsehours":4,"cwx_resolutionhours":24,"cwx_tier":3,"cwx_active":true}'
```
Target the **entity-set (plural) name** (`cwx_slas`), not the logical name; the response returns the full row including its new GUID.

## Create a record binding lookups with `@odata.bind`

```bash
crm --json entity create cwx_tickets --data '{
  "cwx_name":"Laptop won'\''t boot",
  "cwx_priority":3, "cwx_severity":2, "cwx_category":1,
  "cwx_CustomerId@odata.bind":"/accounts(c2c130c3-c05d-f111-b65d-00155d467b90)",
  "cwx_SLA@odata.bind":"/cwx_slas(00d955b7-c05d-f111-b65d-00155d467b90)"
}'
```
The bind target is the navigation property (PascalCase lookup schema name `cwx_SLA` / `cwx_CustomerId`), not the lowercase logical name.

## Update or upsert by id

```bash
crm --json entity update cwx_tickets a41cfedb-c05d-f111-b65d-00155d467b90 \
  --data '{"cwx_resolvedon":"2026-06-01T12:00:00Z"}'
crm --json entity upsert cwx_tickets c8c8f8e4-c05d-f111-b65d-00155d467b90 \
  --data '{"cwx_resolvedon":"2026-06-01T15:30:00Z"}'
```
`update` is a PATCH; `upsert` is a PATCH that creates the record if missing. Both return `{"ok": true}`.

## Catch typo'd field names before the write (`--validate`)

```bash
crm --json entity create cwx_tickets --validate --data '{"cwx_naem":"typo"}'
```
`--validate` runs 1-3 read-only metadata GETs (entity-set → logical name, attribute names, ManyToOne nav-property names) and blocks the write when a payload key is not a known field, returning the offenders plus a suggestion:

```json
{"ok": false, "meta": {"unknown_fields": ["cwx_naem"], "did_you_mean": {"cwx_naem": "cwx_name"}}}
```

Valid `<nav>@odata.bind` keys are checked against the navigation-property names, so a bound lookup is never a false positive. It's opt-in (the GETs cost 1-3 round-trips) and composes with `--dry-run`: the validation GETs run for real, then the write is previewed. Scope is field-**name** only — option-set values are not validated. Works the same on `entity update`.

## Create from a JSON file (avoid shell-quoting XML payloads)

```bash
crm --json entity create savedqueries --data-file /tmp/cwx_view_active_tickets.json
```
Use `--data-file` for payloads with embedded double quotes (e.g. `savedquery` or `systemform` rows whose columns contain XML). Add `--no-return` for rows that aren't readable until published (appmodule/sitemap, §11).
