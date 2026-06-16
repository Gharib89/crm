# How-to: entity

Record CRUD recipes, taken from the CRMWorx build (§3). See the
[CLI reference](../reference/cli.md) for every flag.

## Create a record

```bash
crm --json entity create cwx_slas --data '{"cwx_name":"Gold 4h/24h","cwx_responsehours":4,"cwx_resolutionhours":24,"cwx_tier":3,"cwx_active":true}'
```
Target the **entity-set (plural) name** (`cwx_slas`), not the logical name; the response returns the full row including its new GUID, plus the normalized id keys `_entity_id` (the GUID) and `_entity_id_url` (its Web API URL) — the single, entity-agnostic place to read a written record's id across `create`/`update`/`delete`/`get` (ADR 0008). `@odata.*` protocol keys are stripped from the curated record.

## Create a record binding lookups with `@odata.bind`

```bash
crm --json entity create cwx_tickets --data '{
  "cwx_name":"Laptop won'\''t boot",
  "cwx_priority":3, "cwx_severity":2, "cwx_category":1,
  "cwx_CustomerId@odata.bind":"/accounts(00000000-0000-0000-0000-000000000014)",
  "cwx_SLA@odata.bind":"/cwx_slas(00000000-0000-0000-0000-000000000001)"
}'
```
The bind target is the navigation property (PascalCase lookup schema name `cwx_SLA` / `cwx_CustomerId`), not the lowercase logical name.

You only hand-write the bind when you know the GUID up front. If the lookup value already came from a read — the server's READ shape `_<attr>_value` (raw GUID, as `data export` / `query odata` emit) — `entity create` and `entity upsert` auto-rewrite it into the `<nav>@odata.bind` write shape, resolving the nav property and target set from relationship metadata. So a payload built from an exported row imports unedited. Read-only lookup values (e.g. `_createdby_value`) and read-only OData annotation keys are dropped; a `null` `_<attr>_value` clears the lookup; a payload already in write shape (no `_value`/annotation keys) is untouched (no metadata fetch). **Polymorphic lookups (`customerid`/`ownerid`) bind only when the record carries the `@Microsoft.Dynamics.CRM.lookuplogicalname` annotation naming the concrete target — without it the lookup is silently dropped** (matching `entity clone`'s never-copy-`ownerid` behavior), so export with annotations to round-trip one. See [How-to: data](data.md#round-tripping-an-export-read-shape-lookups-auto-rebind).

## Update or upsert by id

```bash
crm --json entity update cwx_tickets 00000000-0000-0000-0000-000000000011 \
  --data '{"cwx_resolvedon":"2026-06-01T12:00:00Z"}'
crm --json entity upsert cwx_tickets 00000000-0000-0000-0000-000000000015 \
  --data '{"cwx_resolvedon":"2026-06-01T15:30:00Z"}'
```
`update` is a PATCH; `upsert` is a PATCH that creates the record if missing. Both return `{"ok": true}`.

## Upsert by alternate key (`entity upsert --key`)

When you do not have the primary GUID, `--key` lets you upsert by a
natural/alternate key defined on the entity. Omit the positional `RECORD_ID` —
the key values are read directly from `--data`, and `--key` with a positional
GUID is a usage error (exit 2).

```bash
# Single-attribute alternate key
crm --json entity upsert accounts --key accountnumber \
  --data '{"accountnumber":"ACC-001","name":"Contoso Ltd"}'

# Composite alternate key (comma-separated)
crm --json entity upsert cwx_slas --key cwx_tier,cwx_region \
  --data '{"cwx_tier":3,"cwx_region":"EU","cwx_name":"Gold EU"}'
```

Key attributes are **stripped from the request body** — Dataverse identifies the
record from the URL key segment (`accounts(accountnumber='ACC-001')`) and
discards (or on create, copies from the URL) those fields, so the CLI omits them
from the body for you.

`--key` validates that the named attribute(s) form a **defined** alternate key on
the entity before issuing the PATCH — an unknown or unregistered combination
returns a clean error listing the defined keys (names + attributes). If the key's
index is not yet active (asynchronous activation after creation), the server may
return a 404; wait for the index status to become `Active` (`crm metadata keys
<entity>`).

On success the envelope carries `_entity_id_url` from the server's
`OData-EntityId` header — for an alternate-key upsert that URL is the key path
itself (no primary GUID, so no `_entity_id`):

```json
{"ok": true, "data": {"_entity_id_url": ".../accounts(accountnumber='ACC-001')"}}
```

List the alternate keys defined on an entity with:

```bash
crm --json metadata keys accounts
```

Both `create` and `update` accept `--return-record` (echo the full row back) and `--no-return` (a minimal ack, no echoed row) — only the default differs: `create` echoes the row unless you pass `--no-return`, `update` does not echo the record unless you pass `--return-record` (it still returns the standard `{"ok": true}` ack). Passing both at once is a usage error.

## Concise human output, and `--full` to expand

In **human** mode (no `--json`), `entity get <set> <id>` and `entity create <set> --data …` render a record *concisely* by default (ADR 0008 — Record render modes): the `@odata.*` protocol keys (`@odata.context`/`@odata.etag`/…) and any null/empty fields are dropped, and the normalized id (`_entity_id`) is hoisted to the top, followed by the primary-name attribute when that entity's metadata is already cached. The effect is that a `get` on an account shows the handful of populated fields a person actually wants instead of the ~190-line dump (led by `@odata.context`/`@odata.etag`, name and id buried) it used to print.

```text
crm entity get accounts 11111111-1111-1111-1111-111111111111
# _entity_id : 11111111-1111-1111-1111-111111111111
# name       : Contoso Ltd
# telephone1 : +1-555-0100
# ...populated business fields only...
```

Pass `--full` (on both `entity get` and `entity create`) to restore the old behavior — **every** field, including nulls and the `@odata.*` plumbing:

```text
crm entity get accounts 11111111-1111-1111-1111-111111111111 --full
# @odata.context : .../$metadata#accounts/$entity
# @odata.etag    : W/"1234567"
# _entity_id     : 11111111-1111-1111-1111-111111111111
# accountid      : 11111111-1111-1111-1111-111111111111
# name           : Contoso Ltd
# ...every field including nulls...
```

The primary-name hoist reads the **warm metadata cache only** — it never adds a metadata round-trip to a plain get/create, so a cold cache simply leaves the name in its natural position. `--full` is a human-mode concept and has **no effect under `--json`**: JSON output is unchanged (still the full curated record; `--minimal` trims the JSON).

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
crm --json entity get cwx_tickets 00000000-0000-0000-0000-000000000011 \
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
# -> {"ok": true, "data": {"_dry_run": true, "would_create": {"entity_set": "accounts", "body": { ... }}}, "meta": {"dry_run": true}}
```

The success envelope matches `entity create` — `data` is the created record with the normalized `_entity_id`/`_entity_id_url` keys, or just `{"_entity_id": "<guid>", "_entity_id_url": "<url>"}` with `--no-return` (same normalized keys, no echoed row). To clone into a specific status, run `entity update <set> <newid> --data '{"statuscode": N}'` after the clone (on **on-prem** create does not honor a status passed in the create body, so `--override statuscode=N` is a documented no-op there).

## Clone a record with its children (`entity clone --with-children`)

```bash
crm --json entity clone accounts 00000000-0000-0000-0000-000000000001 --with-children
```

`--with-children` clones the parent, then the **direct child rows** of every **custom** 1:N relationship where this record is the parent — one level deep, no recursion. "Custom" is a pure metadata signal (`IsCustomRelationship == true`), so a custom lookup on a system entity still qualifies and no entity-name lists are baked in. `--skip-child-entity <logical>` (repeatable) prunes a child entity from that default — this is how you exclude an org-specific, plugin-derived table the CLI can't know about.

Each child row is cloned with the same never-copy and lookup-rebind rules as the parent, with one addition: **every** lookup on a child whose value equals the source parent is repointed to the **new** parent (not just the relationship's own referencing attribute); other lookups copy as-is. `--override`/`--unset` apply to the **parent only**.

There is **no rollback** (ADR 0007). If the parent create fails, nothing else happens and the run is a clean failure. If a *child* create fails, the verb does not abort and does not delete what it already made — it records the failure and continues. The envelope then reports `ok: false` with the ids it did create in `meta.created` (the parent plus per-entity child ids) and the problems in `data.failures` (each `{entity, source_id, reason}`):

```bash
crm --json entity clone accounts <id> --with-children
# ok:false ->
# "data":   { "failures": [{"entity": "contoso_line", "source_id": "<src>", "reason": "..."}] }
# "meta":   { "created": { "parent": "<new-id>", "children": { "contoso_invoice": ["<id>", "<id>"] } } }
```

Recover by cloning the named failed rows individually — **never re-run the whole verb**, since the parent already exists. `--dry-run` previews everything read-only: the parent keeps its full `would_create` body and a `children` list reports the per-entity row counts (`{"entity": ..., "would_create": N}`), with skipped entities marked `{"entity": ..., "skipped": true}` so the preview shows what won't happen too:

```bash
crm --json --dry-run entity clone accounts <id> --with-children --skip-child-entity contoso_note
# -> "children": [{"entity": "contoso_invoice", "would_create": 7}, {"entity": "contoso_note", "skipped": true}]
```
