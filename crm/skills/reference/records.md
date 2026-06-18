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

The positional arg is the URL path. Three forms are accepted:

| Form | Example |
|------|---------|
| bare entity set | `contacts`, `solutions` |
| bound-function path | `RetrieveAppComponents(AppModuleId=<guid>)` |
| metadata path | `EntityDefinitions(LogicalName='account')/Keys` |

Put OData options in `--select`/`--filter`/etc., never inline.
A `?` or `$` in the arg (e.g. `contacts?$select=fullname`) returns a `validation`
error client-side — recover by moving the params onto flags, not by retrying the URL.

By default `--json` already strips `@odata.*` protocol keys but keeps formatted-value
annotations (`*@FormattedValue`, `*@...lookuplogicalname`). `--minimal` strips **all**
`@`-containing keys (including those formatted values) from each record, keeping
business fields, `_*_value` lookup GUIDs, and the primary id — **the form to chain
downstream.** It is available on `query odata/fetchxml/saved/user` and `entity get`.
List verbs return a **bare array** in `data` (rows at `data[0]`); paging is in
`meta.next_link`/`meta.count`, never an OData envelope in `data`.

**Paging gotchas (`--all` / `--max-records`).**
When page-following is active (`--all` or `--max-records`), `meta.next_link` is
**absent** from the result even when the org has more rows — the signal means
"cursor followed as far as requested", not "no more pages".  When `--max-records`
actually hit the cap, `meta.truncated: true` is set; if it is absent (or false)
the follow reached exhaustion before the cap.  A valid resume cursor cannot be
synthesised after a capped follow — if you need to paginate manually, use the
default single-page behaviour and thread `meta.next_link` yourself.

```bash
crm --json query odata contacts \
    --filter "statecode eq 0" --select fullname,emailaddress1 --top 5 --minimal
```

**Change tracking — incremental sync.**
Two mutually exclusive modes let you retrieve only what changed since a prior
snapshot rather than re-querying the full table.

*Initiate:* the first tracked call surfaces two extra keys in `meta`:
`meta.delta_link` (the opaque resume URL) and `meta.delta_token` (the bare
token — pass this to the next call).

*Resume:* supply the saved token to fetch only rows created/updated/deleted
since the prior round; each round returns a fresh `meta.delta_link` /
`meta.delta_token` to chain from.

*Deletions* arrive as rows shaped `{"id": "<guid>", "reason": "deleted"}` —
the per-row `$deletedEntity` context is stripped by the normal `@odata.*` strip.

*Gotchas:*
- Change tracking must be enabled on the table (on by default for many system
  tables such as account/contact; check table settings if you get an error).
- The Dataverse API forbids `$filter`, `$orderby`, `$expand`, `$top`, and page
  following (`--all`/`--max-records`) alongside change tracking — the CLI
  rejects those combinations client-side before any request.
  `--select`/`--count`/`--page-size` are compatible.

## Count rows — `query count`

```bash
crm --json query count accounts  # -> {"ok": true, "data": {"entity": "account", "count": 5432}}
```

Accepts either the entity-set name (`accounts`, like every other `query` verb) or the
logical name (`account`), case-insensitively; both resolve to the logical name that
`RetrieveTotalRecordCount` is keyed by, and `data.entity` always reports that resolved
logical name. The total is a server-side cached snapshot, so it can lag recent
inserts/deletes (cheap, whole-table; there is **no `--filter`**). For an **exact or
filtered** count, request `$count` on a live OData query instead:

```bash
crm --json query odata accounts --filter "statecode eq 0" --top 1 --count
# the live count is surfaced at meta.count (← @odata.count)
```

## CRUD — create → update → delete

```bash
crm --json entity create contacts --data '{"firstname":"Rafel","lastname":"Shillo"}'
# returns {"ok": true, "data": {"contactid": "<guid>", ..., "_entity_id": "<guid>", "_entity_id_url": "<url>"}}

crm --json entity update contacts <guid> --data '{"telephone1":"+1-555-0100"}'

crm --json entity delete contacts <guid> --yes
# returns {"ok": true, "data": {"deleted": true, "_entity_id": "<guid>", "_entity_id_url": "<url>"}}
```

For a large or quote-heavy single-record payload (many attributes, embedded
quotes, or one row pulled from a bulk file), pass it via `--data-file` on
`entity create`/`update`/`upsert` instead of inline `--data` — a file sidesteps
shell quoting and command-line length limits.

**Normalized id — read the written record's GUID from `_entity_id` (with
`_entity_id_url`) on `create`, `update`, `delete`, `clone`, and `entity get`** — one
entity-agnostic key, no need to know the per-entity primary-key attribute
(`accountid` vs `activityid`). The leading underscore marks it CLI-synthesized; the
genuine PK attribute still appears in a create/get's full record. List rows are
**not** given `_entity_id` (each row carries its own PK). `@odata.*` protocol keys
are stripped from every curated `data` payload.

## FetchXML query

`ENTITY_SET` is optional — omit it and the entity-set name is derived from
`<entity name="...">` via one extra metadata GET (EntityDefinitions).
Pass it explicitly to skip that call.

```bash
# No ENTITY_SET — derived at runtime (one extra GET)
crm --json query fetchxml --xml '
<fetch top="10">
  <entity name="account">
    <attribute name="name"/>
    <attribute name="industrycode"/>
    <filter><condition attribute="statecode" operator="eq" value="0"/></filter>
  </entity>
</fetch>'

# Explicit ENTITY_SET — no extra GET
crm --json query fetchxml accounts --xml '<fetch>...</fetch>'
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

`disassociate` and `clear-lookup` are destructive: pass `--yes` when calling
non-interactively (omitting it aborts with `{"ok":false,"error":"aborted by user"}`,
exit 1). On a TTY the verb prompts for confirmation instead.

## Audit a record's related data — `entity children`

Before cloning, deleting, or auditing a record, answer "what related data does this
actually have?" via chunked **`$batch`** instead of one counted query per relationship
(an account has ~130 one-to-many relationships):

```bash
crm --json entity children accounts <guid> --non-empty
# -> data: [{"entity":"contact","attribute":"parentcustomerid","set":"contacts","count":1}, ...]
```

Enumerates the 1:N relationships where the record is the **parent** (referenced) side
and counts child rows per relationship via chunked `$batch` (round trips are
O(relationships / chunk), not one-per-relationship). `--non-empty` drops zero-count
rows; `--filter-entities REGEX` restricts to matching child **logical names** before
querying (fewer requests). Read-only — composes with `--dry-run`.

**Gotcha — uncountable children.** Some system entities reject `RetrieveMultiple`
(activity-feed types `postregarding`/`postrole`; `sharepointdocument` when SharePoint
is off). They return `count: null` + an `error` string instead of aborting the audit,
and `--non-empty` keeps them (unknown ≠ empty). The count uses `?$count=true&$top=1`
(reads `@odata.count`) — **not** `/$count?$filter=`, which on-prem 9.1 rejects with
"no property '_x_value' on type 'Edm.Int32'". M:N counts and delete-impact analysis are
out of scope.

## Clone a record (`entity clone`)

```bash
crm --json entity clone accounts <guid>
```

Copies one record into a new one. The copied columns are the source's create-valid
attributes minus the **never-copy set**: every `Uniqueidentifier`-typed column (primary
id + `address1_addressid`-class child ids, dropped by type so no per-entity lists),
`statecode`/`statuscode`, `ownerid`, `overriddencreatedon`. The clone lands in the
server-default state, owned by the caller. Set lookups are rebound to the **same parent**
— resolution is annotation-driven (the source is retrieved with annotations; the
per-value `associatednavigationproperty` gives the exact case-sensitive nav and
`lookuplogicalname` gives the target set), so single-target and polymorphic lookups both
bind without guessing nav casing.

**Contract.** All lookup/`--unset` resolution is a **clone pre-flight** that runs before
the one create write: failures (an unresolvable lookup, an `--unset` of a non-attribute)
are batched into a single `{ok:false}` and the org is untouched — a lookup is never
silently dropped. `--dry-run` runs the same pre-flight and returns the resolved body
instead of writing:

```json
{"ok": true, "data": {"_dry_run": true, "would_create": {"entity_set": "accounts", "body": {...}}}, "meta": {"dry_run": true}}
```

Success matches `entity create` (`data` = created record with `_entity_id`/`_entity_id_url`, or `{_entity_id, _entity_id_url}` with `--no-return` — same normalized keys).

**Gotcha — status on clone.** The clone always lands in the server-default state; to set
a status, `entity update <set> <newid> --data '{"statuscode":N}'` *after* the clone.
On **on-prem**, a status in the create body is ignored, so passing it via `--override` is
a no-op there. Re-add any never-copy field (or override a cloned value) with
`--override`; its key passes raw, so an `@odata.bind` key works.

**`--with-children`.** Also clones the direct child rows of every *custom* 1:N
relationship where the record is the parent (one level, no recursion; custom = the
`IsCustomRelationship` metadata flag, so a custom lookup on a system entity counts).
Child rows follow the same never-copy/lookup rules, plus: **every** child lookup whose
value equals the source parent is repointed to the *new* parent (not just the
relationship's own attribute); other lookups copy as-is. `--override`/`--unset` hit the
parent only.

**Contract + gotcha — no rollback (ADR 0007).** Parent-create failure → clean
`{ok:false}`, nothing else done. A *child*-create failure neither aborts nor deletes
what's already made — it is recorded and the rest continue. The envelope then carries the
created ids in `meta.created` and the problems in `data.failures`, and `ok` flips to false:

```json
{"ok": false,
 "data": {"failures": [{"entity": "contoso_line", "source_id": "<src>", "reason": "..."}]},
 "meta": {"created": {"parent": "<new-id>", "children": {"contoso_invoice": ["<id>"]}}}}
```

Recover by cloning the named failed rows individually — **never re-run the whole verb**
(the parent already exists). `--dry-run` is read-only: the parent keeps `would_create`
and a `children` list reports per-entity counts (`{"entity","would_create":N}`), skipped
entities marked `{"entity","skipped":true}`.

## Record-create payloads (`@odata.bind`)

When constructing `entity create`/`update` payloads, lookup fields require an
`@odata.bind` suffix on the **single-valued navigation-property name**, **not** the
logical attribute. That name is metadata-defined and **case-sensitive** — it is NOT a
predictable transform of the attribute: custom lookups often surface a PascalCase
relationship name (e.g. `cwx_CustomerId@odata.bind`) while system lookups commonly
match the lowercase attribute (e.g. `primarycontactid@odata.bind`). Never guess it —
take it from `crm metadata describe <entity>` (`bind_key` per lookup) or
`crm metadata relationships <entity>` (`ReferencingEntityNavigationPropertyName`). A picklist
bound to a global option set binds through `GlobalOptionSet@odata.bind`, and **on-prem
9.1 requires the option set's `MetadataId` GUID there** (the `Name` alternate key is
rejected). `crm metadata describe <entity>` hands you the exact `bind_key` per lookup
and the `global_optionset_id` per global-bound picklist — see `reference/metadata.md`.

Add `--validate` to `entity create`/`entity update` to field-name-check the payload
before the write. It runs 1-3 read-only metadata GETs and blocks unknown fields with
`{ok:false, meta:{unknown_fields, did_you_mean}}`; valid `<nav>@odata.bind` keys are
not flagged. It composes with `--dry-run`. Scope is field-**name** only — option-set
values are not validated (look them up first; see `reference/metadata.md`).

On **`entity create`** only, `--validate` additionally warns when the payload contains
the entity's primary id (e.g. `accountid`). The write still proceeds — explicit-GUID
creates are intentional — but the warning flags the copy-a-record footgun where
`metadata describe` carries the primary id into the payload:

```json
{"ok": true, "data": {...}, "meta": {"warnings": ["payload contains primary id 'accountid' — remove it unless you intend to create with an explicit GUID"]}}
```

**For unattended writes, validate-first is the recommended default.** Without
`--validate`, an unknown field surfaces only as raw OData server noise (e.g.
`Does not support untyped value in non-open type`) instead of the clean
`unknown_fields`/`did_you_mean` envelope — so an agent gets a cryptic failure it
cannot act on. Prefer validate-first (optionally with `--dry-run`) for any
agent-driven create/update.

### Round-tripping a READ-shape lookup (no hand-`@odata.bind`)

You don't have to hand-write the `<nav>@odata.bind` above when the lookup value
already came from a read. `data import`, `entity create`, and `entity upsert`
auto-rewrite any lookup that arrives in the server's **READ shape**
`_<attr>_value` (the raw-GUID form that `data export` / `query odata` emit) into
the WRITE shape `<nav>@odata.bind: "/<set>(<guid>)"`, resolving the nav property
and target set from relationship metadata. So an exported row imports unedited —
no manual `@odata.bind` surgery. Details:

- A read-only lookup value (e.g. `_createdby_value`) is **dropped** — it can't be
  written. Read-only OData annotation keys (`@odata.etag`, `@odata.context`,
  `@OData.Community.Display.V1.FormattedValue`, per-value annotations) are
  stripped; a hand-written `<nav>@odata.bind` you supply is preserved as-is.
- A `null` `_<attr>_value` **clears** the lookup (`<nav>@odata.bind: null`).
- A payload already in write shape (plain columns + your own `@odata.bind`, no
  `_value`/annotation keys) is left untouched — no metadata fetch happens.

**Gotcha — polymorphic lookups (`customerid`, `ownerid`, …) need annotations.**
A Customer/Owner lookup binds to the concrete target named by its
`…@Microsoft.Dynamics.CRM.lookuplogicalname` annotation. When that annotation is
**absent** the lookup is **silently dropped** (not bound, not an error) so the
rest of the record still round-trips — matching `entity clone`'s never-copy-`ownerid`
behavior. A plain `data export` carries **no** annotations and `ownerid` is on
every record, so by default polymorphic lookups won't round-trip: **export with
annotations** (`query odata` with annotations, or an annotated retrieve) if you
need them rebound.

This is lookup-only: non-lookup read-only / unique scalar fields are **not**
stripped, so a whole-record export may still be rejected on those (a separate
concern). There is no export-side "import-ready" flag.

## Upsert by alternate key (`entity upsert --key`)

When no primary GUID is known, match by a natural/alternate key. Omit the
positional `RECORD_ID` — key values are read from `--data`. Passing both a
GUID and `--key` is a usage error (exit 2).

**Body-stripping.** The key attribute(s) are automatically removed from the
request body before the PATCH. Dataverse identifies the record from the URL key
segment (`accounts(accountnumber='ACC-001')`) and ignores (or on create, copies
from the URL) those fields — sending a differing body value is rejected by the
server. You do not need to remove them yourself.

**Pre-flight validation.** The CLI calls `metadata keys` before the PATCH and
verifies the named attribute(s) match a **defined** alternate key on the entity.
An unknown or unregistered combination returns a clean error listing defined
keys — the PATCH is never issued. If the key's index is not yet `Active`
(asynchronous activation in Dataverse after key creation), the server returns a
404; check index status with `crm --json metadata keys <entity>` and wait for
`"index_status": "Active"`.

**Composite keys.** Multiple attributes (comma-separated) must exactly match the
attribute set of one defined key — a subset or superset is rejected. The CLI
reorders the attributes to the **metadata's canonical order** regardless of the
order you listed them, so the URL path is stable across calls.

**Success envelope.** A 204 with the server's `OData-EntityId` header yields
`data._entity_id_url` — for an alternate-key upsert that URL is the key path
itself (`accounts(accountnumber='ACC-001')`). It has no bare `(<guid>)` segment,
so (unlike a primary-key write) there is **no** `_entity_id`:

```json
{"ok": true, "data": {"_entity_id_url": ".../accounts(accountnumber='ACC-001')"}}
```

Only when the server omits the header does it fall back to
`{"upserted": true, "key": "accountnumber='ACC-001'"}`.

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

# Upsert by alternate key; key column(s) stripped from each row's body
crm data import accounts accounts.jsonl --mode upsert --key accountnumber

# Composite alternate key
crm data import cwx_slas slas.jsonl --mode upsert --key cwx_tier,cwx_region

# CSV import (best-effort coercion; prefer JSONL for IDs / postal codes / lookups)
crm data import cwx_tickets tickets.csv

# Non-transactional + continue-on-error (requires --no-transaction)
crm data import accounts large.jsonl \
    --chunk-size 50 --no-transaction --continue-on-error

# Dry-run preview — zero writes, summary shows imported:0 dry_run:true
crm --json --dry-run data import accounts records.jsonl
```

**`--mode upsert` requires exactly one of `--id-column` or `--key`; passing
both is a usage error. `--key` is rejected outside `--mode upsert`.**

**Alternate-key import gotcha.** `--key` resolves and validates the key against
entity metadata before the first row is processed. If the alternate key's index
is not yet `Active`, the server returns 404 per row (not a bulk failure — each
row fails individually in `data.failures`). Check index status first:

```bash
crm --json metadata keys <entity>
# look for "index_status": "Active" on the matching key
```

Output: `{imported, failed, chunks, entity_set, mode, dry_run, format, failures}`.
`failures` is **always present** (empty `[]` when nothing failed); each entry is
`{index, id?, status, error}` — `index` is the 1-based input-row position, `id` the
record GUID (only under `--mode upsert`), `status` the server HTTP status, `error` the
server message. `failed > 0` also surfaces a `meta.warnings` count advisory; **exit code
is 0 on partial failure** — read `data.failures` for which rows failed and why (no need
to re-issue rows to discover it), don't rely on `$?`. (This is a different per-row shape
from `entity clone`'s `{entity, source_id, reason}` above, and here `ok` stays `true`.)

**Alternate-key collision hint.** A row that fails with the alternate-key uniqueness
code (`0x80060892`) additionally carries best-effort enrichment fields on its
`failures` entry:

```json
{
  "index": 3,
  "status": 412,
  "error": "A record with matching key values already exists.",
  "alternate_keys": [
    {
      "name": "accountnumber_key",
      "schema_name": "accountnumber_key",
      "attributes": ["accountnumber"],
      "payload_values": {"accountnumber": "ACC-001"}
    }
  ]
}
```

`alternate_keys` lists every defined alternate key on the entity (each
`{name, schema_name, attributes, payload_values}`), where `payload_values` is the
intersection of the key's attribute names with the failing row's payload. When the
row's payload also contains the entity's primary-id attribute, a `primary_id_hint`
string is added to warn that the server returns the same error code for a
primary-key collision too. The schema is fetched at most once per import run
(identical for every row). Both fields are absent when the schema lookup fails or
the row's code is different — enrichment is strictly best-effort and never masks
the original error.

## Raw `$batch` — `crm batch`

`crm batch <file.json>` runs a hand-authored `$batch` directly — the escape hatch for
mixed/cross-entity bulk work that `data import` (single-entity) can't express, e.g.
deleting many records in one round-trip. The file is a **JSON array of operation
objects**, each carrying a `method` and `url` (plus a `body` on writes):

- `method` — `GET` | `POST` | `PATCH` | `DELETE`.
- `url` — a **bare relative path** (`contacts(<guid>)`, `accounts`), no leading slash.
- `body` — JSON object; **required** on `POST`/`PATCH`, **rejected** on `GET`/`DELETE`.
- optional `headers` (object of string values) and `content_id` (str/int, to reference a
  just-created record from a later op in the same changeset via `$<content_id>`).

**Gotcha — `url` must not begin with `/`.** A leading slash resolves against the host
root, not the Web API path, and 404s. `crm batch` blocks it client-side before any
request with a `validation` error telling you the `url` must be a bare relative path —
fix the file, don't retry.

Minimal bulk delete — two contacts, atomic (see grouping note below):

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

Transaction grouping (default mode): each run of **consecutive writes**
(`POST`/`PATCH`/`DELETE`) is wrapped in one atomic changeset (all-or-nothing rollback),
while every `GET` stays a top-level op and breaks the run — so a file that interleaves
reads and writes produces *several* changesets, not one. An all-write file like the
bulk-delete above is therefore a single atomic unit. `--no-transaction` drops the
changesets and sends every op top-level; `--continue-on-error` (which requires
`--no-transaction`) keeps going past a failed op. **Exit code is 0 even when some ops
fail** — read each result's `status` and `meta.failed`, don't rely on `$?`.

## Ad-hoc OData function / action

```bash
crm --json action function RetrieveCurrentOrganization \
    --params '{"AccessType":"Default"}'
```

`action function` issues a **GET** (read-only); `action invoke` issues a **POST**
(state change, journalled). Both run unbound by default and can bind to a
collection or a single record (e.g. a function bound to a `systemusers` record).
Pick `function` vs `invoke` by the operation's OData kind, not by whether it
binds. Run `crm describe action` for the exact bind flags.
