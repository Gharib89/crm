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

Both `create` and `update` accept `--return-record` (echo the full row back) and `--no-return` (a minimal ack, no echoed row) — only the default differs: `create` echoes the row unless you pass `--no-return`, `update` does not echo the record unless you pass `--return-record` (it still returns the standard `{"ok": true}` ack). Passing both at once is a usage error.

## Catch typo'd field names before the write (`--validate`)

```bash
crm --json entity create cwx_tickets --validate --data '{"cwx_naem":"typo"}'
```
`--validate` runs 1-3 read-only metadata GETs (entity-set → logical name, attribute names, ManyToOne nav-property names) and blocks the write when a payload key is not a known field, returning the offenders plus a suggestion:

```json
{"ok": false, "meta": {"unknown_fields": ["cwx_naem"], "did_you_mean": {"cwx_naem": "cwx_name"}}}
```

Valid `<nav>@odata.bind` keys are checked against the navigation-property names, so a bound lookup is never a false positive. It's opt-in (the GETs cost 1-3 round-trips) and composes with `--dry-run`: the validation GETs run for real, then the write is previewed. Scope is field-**name** only — option-set values are not validated. Works the same on `entity update`.

On **`entity create`**, `--validate` also warns when the payload contains the entity's primary id attribute (e.g. `accountid`). The warning does not block the write — creating with an explicit GUID is intentional in some workflows — but it catches the common footgun of copying a record whose primary id was carried over from `metadata describe`:

```json
{"ok": true, "data": {...}, "meta": {"warnings": ["payload contains primary id 'accountid' — remove it unless you intend to create with an explicit GUID"]}}
```

This warning is not emitted for `entity update` (setting the primary id on an update is silently ignored by the server, not a footgun).

## Assert a field value after a write (`--expect`)

```bash
crm --json entity get cwx_tickets a41cfedb-c05d-f111-b65d-00155d467b90 \
  --expect statecode=1 --expect statuscode=5
```
The repeatable `--expect ATTR=VALUE` flag turns the retrieve into a self-checking verify step — handy for confirming a state change or an async write actually landed. Each pair passes only if `str(record[ATTR]) == VALUE`; multiple `--expect` flags are AND-gated (every one must match). The first mismatch exits **1** with the offending field under `meta`:

```json
{"ok": false, "error": "Expectation failed: statecode='1' (actual 0)", "meta": {"attr": "statecode", "expected": "1", "actual": 0}}
```

When every pair matches, the command exits **0** and emits the record as usual. The check runs against the **full** record, before any `--minimal` projection. A malformed `--expect` (no `=`) is a usage error (exit 2) raised before the GET.

## Alternate-key duplicate errors (`meta.alternate_keys`)

When `entity create` or `entity update` fails with an alternate-key uniqueness
violation (HTTP 412, error code `0x80060892`), the error envelope gains a
`meta.alternate_keys` array showing each key, its attributes, and the values
from your payload that collided:

```json
{
  "ok": false,
  "error": "Entity Key Code Key violated. A record with the same value ...",
  "meta": {
    "status": 412,
    "code": "0x80060892",
    "category": "duplicate_detected",
    "retryable": false,
    "alternate_keys": [
      {
        "name": "account_code_ak",
        "schema_name": "Account_Code_AK",
        "attributes": ["accountnumber"],
        "payload_values": {"accountnumber": "ACC-001"}
      }
    ]
  }
}
```

If your payload also contains the entity's primary-key attribute (`accountid`
for `account`), a `meta.primary_id_hint` field is added — the server returns
the same `0x80060892` error for a primary-key collision as for an alternate-key
collision.

List the alternate keys for any entity with `crm metadata keys <entity>`.

**v1 limitation:** `payload_values` is populated from plain scalar payload fields only.
Lookup bindings (`field@odata.bind`) are not matched and will not appear in
`payload_values` even if a lookup is part of the alternate key.

## Create from a JSON file (avoid shell-quoting XML payloads)

```bash
crm --json entity create savedqueries --data-file /tmp/cwx_view_active_tickets.json
```
Use `--data-file` for payloads with embedded double quotes (e.g. `savedquery` or `systemform` rows whose columns contain XML). Add `--no-return` for rows that aren't readable until published (appmodule/sitemap, §11).

## Audit a record's related data before clone/delete (`entity children`)

```bash
crm --json entity children accounts 00000000-0000-0000-0000-000000000001 --non-empty
```
`entity children` answers "what related data does this record actually have?" — it enumerates the 1:N relationships where the entity is the **parent** (referenced) side and reports the related-record count per relationship through **chunked `$batch`** (a handful of POSTs) instead of one counted query per relationship (an account has ~130 one-to-many relationships). One row per relationship:

```json
{"ok": true, "data": [
  {"entity": "contact", "attribute": "parentcustomerid", "set": "contacts", "count": 1},
  {"entity": "cwx_ticket", "attribute": "cwx_customerid", "set": "cwx_tickets", "count": 1}
]}
```

- `--non-empty` drops relationships whose count is 0.
- `--filter-entities REGEX` restricts to child entities whose **logical name** matches the regex, applied *before* the counts are issued (fewer requests, not a display filter).

Counts go through `$batch` in chunks, so round trips are O(relationships / chunk-size), not one per relationship. Read-only — composes with `--dry-run` (the GETs run for real). Self-referential relationships (e.g. account `parentaccountid` → account) are ordinary rows.

**Uncountable child entities.** Some system entities reject `RetrieveMultiple` (activity-feed types like `postregarding`/`postrole`, or `sharepointdocument` when SharePoint integration is off). These surface with `count: null` and an `error` string rather than aborting the whole audit:

```json
{"entity": "postregarding", "attribute": "regardingobjectid", "set": "postregardings", "count": null, "error": "The 'RetrieveMultiple' method does not support entities of type 'postregarding'."}
```

`--non-empty` keeps these null rows (unknown ≠ empty). The count itself is issued as `?$count=true&$top=1` (reading `@odata.count`), **not** `/$count?$filter=` — on-prem 9.1 rejects a `$filter` on the `/$count` path segment ("no property '_x_value' on type 'Edm.Int32'"). Scope is 1:N only — many-to-many counts and cascade/delete-impact analysis are out of scope.

## Clone a single record (`entity clone`)

```bash
crm --json entity clone accounts 00000000-0000-0000-0000-000000000001
```

`entity clone` copies one record's values into a new record. It starts from the source's create-valid attributes and drops the **never-copy set** — every `Uniqueidentifier`-typed column (the primary id, plus `address1_addressid`-class child ids, generically — no per-entity lists), `statecode`/`statuscode`, `ownerid`, and `overriddencreatedon`. Each lookup that is set on the source is rebound to the **same parent**: the source is retrieved with annotations, and the per-value `@Microsoft.Dynamics.CRM.associatednavigationproperty` (the exact case-sensitive nav property) and `@...lookuplogicalname` (the target table) are turned into a `<nav>@odata.bind` deep-link — so single-target and polymorphic lookups both bind correctly without guessing nav-property casing. The new record lands in the server-default state and is owned by the caller.

Adjust the new record with `--override FIELD=VALUE` (repeatable) and `--unset FIELD` (repeatable):

```bash
crm --json entity clone accounts 00000000-0000-0000-0000-000000000001 \
    --override name='Contoso (copy)' \
    --unset primarycontactid
```

`--override` re-adds anything from the never-copy set and wins over the cloned value; its key passes raw, so a bind key works too: `--override 'ownerid@odata.bind=/systemusers(<id>)'`. The value is read as JSON when possible (`creditlimit=5000` → number, `donotemail=true` → bool), otherwise as a string. `--unset` drops a field by **logical name** — a lookup's logical name drops the bind it produced.

All lookup resolution and `--unset` validation run as a **clone pre-flight** before the single create write: every offending field (an unresolvable lookup, an `--unset` of a non-existent attribute) is batched into one failure and **the org is untouched**. A lookup is never silently dropped. `--dry-run` runs the same pre-flight and returns the fully resolved create body without writing — the complete fix list against an untouched org:

```bash
crm --json --dry-run entity clone accounts 00000000-0000-0000-0000-000000000001
# -> {"ok": true, "data": {"entity_set": "accounts", "body": { ... }}, "meta": {"dry_run": true}}
```

The success envelope matches `entity create` — `data` is the created record, or just `{"id": "<guid>"}` with `--no-return`. To clone into a specific status, run `entity update <set> <newid> --data '{"statuscode": N}'` after the clone (on **on-prem** create does not honor a status passed in the create body, so `--override statuscode=N` is a documented no-op there). This is a single-record clone; cloning a record together with its child rows (`--with-children`) is a separate follow-up.
