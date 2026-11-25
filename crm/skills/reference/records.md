# Records — query, CRUD, relationships

Read, write, query, and relate records one at a time. Groups: `entity`,
`query`. Flags/choices: `crm <group> --help`. Bulk CSV/JSONL export/import,
server-side BulkDelete, and raw `$batch` files: `reference/bulk.md`.
Clone, `@odata.bind` payloads, upsert and ad-hoc actions:
`reference/records-writes.md`.

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

**Aggregation / group-by (`--apply`).** Because that validator blocks an inline
`$`, `--apply EXPR` is the **only** way to run an OData `$apply` query
(`groupby`/`aggregate`/`distinct`). The `data` array is then whatever the
aggregation returns (the grouped/aggregated rows), **not** the source entity's
columns — don't expect `_entity_id` or the usual fields on those rows.

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

**Default single page is self-describing.** When neither flag is passed and the
server still returns a `meta.next_link` cursor, the envelope also sets
`meta.has_more: true` and adds a `meta.warnings` entry — page through the rest by
following `meta.next_link` (visible with `--json`), or with `--all`/`--max-records`
on `query odata`; don't mistake a single page for the whole result set. If
`--count` was on that same request and `meta.count` lands exactly on the
server's 5000-row ceiling, a second warning flags it as a clamped lower bound,
not the true total (re-run with `--all` for an exact count of this query;
`query count` below gives a whole-table cached figure only, ignoring `--filter`).
The signals fire wherever a response carries an `@odata.nextLink` cursor —
`query odata/saved/user`; `query fetchxml` pages via a fetch cookie (no cursor),
so it never surfaces them, and `--all`/`--max-records`/`--count` are `query
odata`-only.

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
- The Dataverse API forbids `$filter`, `$orderby`, `$expand` and `$top` alongside
  change tracking, and `--max-records` stops before the last page, so the CLI
  rejects those combinations client-side before any request.
  `--select`/`--count`/`--page-size` are compatible.
- The delta token arrives on the last page only. On a table above one page, a
  tracked call without `--all` returns one page (`meta.has_more`) and no
  `meta.delta_token`: pass `--all` (with `--track-changes` or `--delta-token`).

## Count rows — `query count`

```bash
crm --json query count accounts  # -> {"data": {"entity": "account", "count": 5432, "snapshot": true, ...}}
```

Accepts either the entity-set name (`accounts`) or the
logical name (`account`), case-insensitively; both resolve to the logical name that
`RetrieveTotalRecordCount` is keyed by, and `data.entity` always reports that resolved
logical name. The total is a cached server snapshot that can lag recent
inserts/deletes (whole-table; **no `--filter`**): `data.snapshot` is `true` and
`data.exact_count` names the live command. For an **exact or filtered** count,
read a live OData query instead:

```bash
crm --json query odata accounts --filter "statecode eq 0" --all --select accountid
# len(data) is the live count; meta.count clamps at 5000
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
genuine PK attribute still appears in a create/get full record. List rows are
**not** given `_entity_id` (each row carries its own PK). `@odata.*` protocol keys
are stripped from every curated `data` payload.

**Impersonation.** Writes can be issued *as another user* — `--as-user
<systemuser-guid>` on `create`/`update`/`delete`/`upsert` (cloud alternative:
`--as-user-object-id`). The write is attributed to the impersonated user — a
create's `createdby`, an update's `modifiedby` — while the authenticated caller
is recorded in `createdonbehalfby`/`modifiedonbehalfby`.

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

`disassociate` and `clear-lookup` are destructive (`--yes`-gated — SKILL.md).

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

**M:N related rows are still reachable** — through the N:N navigation property
rather than `entity children`: find the relationship's `SchemaName` in
`crm metadata relationships <entity>` (the `ManyToMany` block), then query it as a
collection path:

```bash
crm --json query odata "systemusers(<guid>)/systemuserroles_association" --select name
```
