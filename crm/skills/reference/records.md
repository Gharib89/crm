# Records — CRUD, query, relationships, bulk, ad-hoc actions

Read, write, query, and relate records. Groups: `entity`, `query`, `data`, `action`.
Flags/choices: `crm <group> --help`.

## Identity check

```bash
crm --json connection whoami
# -> {"ok": true, "data": {"UserId": "...", "BusinessUnitId": "...", "OrganizationId": "..."}}
```

## Query — OData filter

```bash
crm --json query odata contacts \
    --filter "statecode eq 0" --select fullname,emailaddress1 --top 5
```

The entity-set arg carries the **path only**; put OData options in `--select`/`--filter`/etc.
A `?` or `$` in it (e.g. `contacts?$select=fullname`) returns a `validation` error
client-side — recover by moving the params onto flags, not by retrying the URL.
(Bound-function paths like `RetrieveAppComponents(...)` have neither and pass through.)

`--minimal` strips OData annotation keys (`@odata.etag`, `*@FormattedValue`,
`*@...lookuplogicalname`) from each `--json` record, keeping business fields,
`_*_value` lookup GUIDs, and the primary id — **the form to chain downstream.** It is
available on `query odata/fetchxml/saved/user` and `entity get`.

```bash
crm --json query odata contacts \
    --filter "statecode eq 0" --select fullname,emailaddress1 --top 5 --minimal
```

## CRUD — create → update → delete

```bash
crm --json entity create contacts --data '{"firstname":"Rafel","lastname":"Shillo"}'
# returns {"ok": true, "data": {"contactid": "<guid>", ...}}

crm --json entity update contacts <guid> --data '{"telephone1":"+1-555-0100"}'

crm --json entity delete contacts <guid> --yes
```

## FetchXML query

```bash
crm --json query fetchxml accounts --xml '
<fetch top="10">
  <entity name="account">
    <attribute name="name"/>
    <attribute name="industrycode"/>
    <filter><condition attribute="statecode" operator="eq" value="0"/></filter>
  </entity>
</fetch>'
```

## Execute a saved system view by GUID

First discover the saved query, then execute it against the entity set:

```bash
crm query odata savedqueries \
    --filter "name eq 'Active Accounts'" --select savedqueryid,name
crm --json query saved accounts <savedqueryid>
```

## Associate / disassociate / lookups

```bash
# Associate to a collection (1:N) — relationship navigation name + related set + id
crm entity associate accounts <account-guid> \
    contact_customer_accounts contacts <contact-guid>

# Set a single-valued lookup (N:1) — parent account on a contact
crm entity set-lookup contacts <contact-guid> \
    parentcustomerid_account accounts <account-guid>

# Disassociate (collection) — supply --related-set + --related-id
crm entity disassociate accounts <account-guid> \
    contact_customer_accounts --related-set contacts --related-id <contact-guid>

# Clear a single-valued lookup
crm entity clear-lookup contacts <contact-guid> parentcustomerid_account
```

## Record-create payloads (`@odata.bind`)

When constructing `entity create`/`update` payloads, lookup fields require an
`@odata.bind` suffix on the **navigation-property name** (the PascalCase schema name,
e.g. `cwx_CustomerId@odata.bind`), **not** the lowercase logical attribute. A picklist
bound to a global option set binds through `GlobalOptionSet@odata.bind`, and **on-prem
9.1 requires the option set's `MetadataId` GUID there** (the `Name` alternate key is
rejected). `crm metadata describe <entity>` hands you the exact `bind_key` per lookup
and the `global_optionset_id` per global-bound picklist — see `reference/metadata.md`.

Add `--validate` to `entity create`/`entity update` to field-name-check the payload
before the write. It runs 1-3 read-only metadata GETs and blocks unknown fields with
`{ok:false, meta:{unknown_fields, did_you_mean}}`; valid `<nav>@odata.bind` keys are
not flagged. It composes with `--dry-run`. Scope is field-**name** only — option-set
values are not validated (look them up first; see `reference/metadata.md`).

## Bulk CSV export

```bash
crm data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue,closeprobability \
    --page-size 500
```

## Bulk import via `$batch`

All writes are routed through `$batch` — the only on-prem bulk mechanism
(`CreateMultiple`/`UpsertMultiple` are cloud-only).

```bash
# Create records from a JSONL file (format inferred from suffix)
crm data import accounts records.jsonl

# Upsert (PATCH by GUID); id-column is stripped from the record body
crm data import contacts contacts.jsonl --mode upsert --id-column contactid

# CSV import (best-effort coercion; prefer JSONL for IDs / postal codes / lookups)
crm data import cwx_tickets tickets.csv

# Non-transactional + continue-on-error (requires --no-transaction)
crm data import accounts large.jsonl \
    --chunk-size 50 --no-transaction --continue-on-error

# Dry-run preview — zero writes, summary shows imported:0 dry_run:true
crm --json --dry-run data import accounts records.jsonl
```

Output: `{imported, failed, chunks, entity_set, mode, dry_run, format}`. `failed > 0`
surfaces a `meta.warnings` advisory; **exit code is 0 on partial failure** — scan
`meta.warnings`, don't rely on `$?`.

## Raw `$batch` — `crm batch`

`crm batch <file.json>` runs a hand-authored `$batch` directly — the escape hatch for
mixed/cross-entity bulk work that `data import` (single-entity) can't express, e.g.
deleting many records in one round-trip. The file is a **JSON array of operation
objects**, each `{"method", "url", "body"}`:

- `method` — `GET` | `POST` | `PATCH` | `DELETE`.
- `url` — a **bare relative path** (`contacts(<guid>)`, `accounts`), no leading slash.
- `body` — JSON object; **required** on `POST`/`PATCH`, **rejected** on `GET`/`DELETE`.
- optional `headers` (object of string values) and `content_id` (str/int, to reference a
  just-created record from a later op in the same changeset via `$<content_id>`).

**Gotcha — `url` must not begin with `/`.** A leading slash resolves against the host
root, not the Web API path, and 404s. `crm batch` blocks it client-side before any
request with a `validation` error (`url must be a bare relative path … not begin with
'/'`) — fix the file, don't retry.

Minimal bulk delete — two contacts in one transactional changeset:

```bash
cat > bulk-delete.json <<'EOF'
[
  {"method": "DELETE", "url": "contacts(00000000-0000-0000-0000-000000000001)"},
  {"method": "DELETE", "url": "contacts(00000000-0000-0000-0000-000000000002)"}
]
EOF
crm --json batch bulk-delete.json
# -> data: [{...,"status":204},{...,"status":204}], meta: {total, success, failed}
```

Default is one transactional changeset (all-or-nothing rollback). `--no-transaction`
sends each op as a top-level operation; `--continue-on-error` (which requires
`--no-transaction`) keeps going past a failed op. **Exit code is 0 even when some ops
fail** — read each result's `status` and `meta.failed`, don't rely on `$?`.

## Ad-hoc OData function / action

```bash
crm --json action function RetrieveCurrentOrganization \
    --params '{"AccessType":"Default"}'
```
