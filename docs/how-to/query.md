# How-to: query

Read and verify recipes, taken from the CRMWorx build (§4). See the
[CLI reference](../reference/cli.md) for every flag.

## Run an OData query with a filter and projection

```bash
crm --json query odata cwx_tickets \
  --filter "cwx_priority eq 3" --select cwx_name,cwx_severity --top 10
```
Returns matching rows as a **bare array** in `data` (`data[0]` is the first row);
queries the entity-set (plural) name. The CLI unwraps the raw OData envelope (ADR
0008): paging moves to `meta.next_link` (← `@odata.nextLink`) and `meta.count`
(← `@odata.count`), and per-row `@odata.*` protocol keys (`@odata.etag`, …) are
stripped. Formatted-value annotations (`*@OData.Community.…`) are kept by default
(`--annotations`; opt out with `--no-annotations`).

The positional argument is the URL path and accepts three forms: a bare entity-set name
(e.g. `contacts`), a bound-function path (e.g. `RetrieveAppComponents(...)`), or a
metadata path (e.g. `EntityDefinitions(LogicalName='account')/Keys`). OData query options
go through `--select`/`--filter`/etc., never inline — a `?` or `$` in the argument is
rejected client-side with a `validation` error before the request.

The bare `in` operator (`workflowid in ('a','b')`) is **OData 4.01** and the
Dataverse Web API (OData 4.0) rejects it — `query odata` detects it and errors
before the request. Use the native `In` query function, or `query fetchxml`:

```bash
crm --json query odata workflows \
  --filter "Microsoft.Dynamics.CRM.In(PropertyName='workflowid',PropertyValues=['<id1>','<id2>'])"
```

## Aggregate / group by with `$apply` (`--apply`)

```bash
# Count records per status
crm --json query odata accounts \
  --apply "groupby((statuscode),aggregate(\$count as count))"

# Sum a measure across a grouping
crm --json query odata opportunities \
  --apply "groupby((ownerid),aggregate(estimatedvalue with sum as total))"
```

`--apply` passes an OData `$apply` expression through to the Web API for
server-side aggregation, grouping, and `distinct`. Because the positional path
validator rejects an inline `$` (see above), `--apply` is the **only** way to run
a `$apply` query. The result `data` shape is whatever the aggregation returns
(the grouped/aggregated rows), wrapped in the standard envelope — not the source
entity's columns.

## Follow pagination automatically (`--all` / `--max-records`)

By default `query odata` returns a single server page and exposes the cursor in
`meta.next_link` when more rows exist.  Two opt-in flags follow the cursor
automatically:

**`--all`** — follows every `@odata.nextLink` to exhaustion and merges all
pages into one `data` array.  `meta.next_link` is absent in the result (paging
was followed completely).

```bash
crm --json query odata contacts --filter "statecode eq 0" --select fullname --all
```

**`--max-records N`** — follows pages only until N total rows are accumulated,
then stops.  The `data` array contains at most N rows.  When the cap was
actually hit (more rows existed beyond what was returned), the envelope carries
`meta.truncated: true`.  `meta.next_link` is absent — a resume cursor is not
emitted because the final page may have been sliced to reach the exact cap.
`--max-records` implies page-following on its own; combining it with `--all`
adds the cap as a bound on the otherwise-unbounded follow.

```bash
crm --json query odata contacts --filter "statecode eq 0" --select fullname \
    --max-records 200
# meta.truncated: true when more than 200 rows matched
```

Default behaviour (neither flag) still returns a single server page — the row
`data` is unchanged from pre-flag behaviour — with `meta.next_link` present when
more pages exist. Use `--page-size` to control how many rows the server puts on
each page. That default page is now self-describing in `meta` (see below).

**Self-describing single page.** When the default (non-`--all`) read comes back
with a `meta.next_link` cursor, the envelope also sets `meta.has_more: true` and
appends a `meta.warnings` advisory ("Returned one server page; more rows exist —
use --all or --max-records to enumerate."). If `--count` was requested and the
returned `meta.count` lands exactly on the server's standard-table ceiling of
5000 while a cursor is present, a second warning flags that count as a clamped
lower bound, not an exact total — use `query count` or `--all` to get the real
number. A query that fits in a single page (no cursor) gets neither `has_more`
nor a warning, and an honest small `--count` is unaffected. This applies to
`query odata`, `query fetchxml`, `query saved`, and `query user` alike.

## Strip annotations for token-efficient JSON (`--minimal`)

```bash
crm --json query odata cwx_tickets \
  --filter "cwx_priority eq 3" --select cwx_name,cwx_severity --annotations --minimal
```
By default the curated payload already strips `@odata.*` protocol keys (etag/context/…) but **keeps** formatted-value annotations (`*@OData.Community.Display.V1.FormattedValue`, `*@…lookuplogicalname`). In `--json` mode `--minimal` goes further: it drops *every* `@`-containing key (including those formatted values) from each record, keeping business fields, `_*_value` lookup GUIDs, and the primary id. Paging stays in `meta` (`meta.count`/`meta.next_link`) regardless. It is a no-op in human/table mode and also works on `query fetchxml`, `query saved`, `query user`, and `entity get`.

## Run a FetchXML query

```bash
# ENTITY_SET can be omitted — derived from <entity name="..."> via one metadata GET
crm --json query fetchxml --xml '
<fetch top="20">
  <entity name="cwx_ticket">
    <attribute name="cwx_name"/>
    <attribute name="cwx_priority"/>
    <order attribute="cwx_name"/>
  </entity>
</fetch>'

# Pass ENTITY_SET explicitly to skip the resolution GET
crm --json query fetchxml cwx_tickets --xml '<fetch>...</fetch>'
```

FetchXML is the server-side XML query language; the `<entity name>` attribute is the
logical name, while the entity-set name (the URL path) is the OData plural name.
When `ENTITY_SET` is omitted, the logical name is parsed from `<entity name="...">` and
resolved to the entity-set name via `EntityDefinitions` — one extra metadata GET.
If the XML has no `<entity name="...">`, pass `ENTITY_SET` explicitly.

## Track changes and resume from a delta token (`--track-changes` / `--delta-token`)

Dataverse change tracking lets you retrieve only the rows that were created,
updated, or deleted since a prior snapshot — useful for sync and incremental
export.  Change tracking must be enabled on the table (it is on by default for
many system tables including `account` and `contact`; list the enabled tables
with `crm --json query odata EntityDefinitions --select LogicalName --filter
"ChangeTrackingEnabled eq true"`).

**Initiate a tracked query** — add `--track-changes` to any `query odata` call.
The response envelope carries two extra keys in `meta`:

- `meta.delta_link` — the opaque resume URL (`@odata.deltaLink`) for the next
  round.
- `meta.delta_token` — the bare `$deltatoken` value lifted out of that URL
  (pass this to `--delta-token` on the next call instead of parsing the link
  yourself).

```bash
crm --json query odata contacts --track-changes --select fullname,statecode
# meta.delta_link:  "https://.../contacts?$deltatoken=<tok>"
# meta.delta_token: "<tok>"
```

**Resume from a prior token** — pass the saved `meta.delta_token` as
`--delta-token`.  Only rows created, updated, or deleted since the prior round
are returned.  Each round surfaces a fresh `meta.delta_link` / `meta.delta_token`
to chain from:

```bash
PREV_TOKEN=$(crm --json query odata contacts --track-changes --select fullname | jq -r .meta.delta_token)
crm --json query odata contacts --delta-token "$PREV_TOKEN" --select fullname
```

**Deletions** arrive as rows shaped `{"id": "<guid>", "reason": "deleted"}` —
the per-row `$deletedEntity` context is stripped along with other `@odata.*`
keys by the normal envelope normalisation.

**Conflicting options.**  The Dataverse Web API forbids system query options
alongside change tracking.  `--track-changes` and `--delta-token` both reject
combination with `--filter`, `--orderby`, `--expand`, `--top`, `--all`, and
`--max-records`; the command errors client-side before any request.
`--select`, `--count`, and `--page-size` are compatible.
`--track-changes` and `--delta-token` are mutually exclusive with each other.

## Call a bound function or metadata path on the URL path

`query odata` accepts three forms for its positional argument — all pass through to the
Web API as the URL path; OData query options always go through the flags:

| Form | Example |
|------|---------|
| bare entity set | `contacts`, `solutions` |
| bound-function path | `RetrieveAppComponents(AppModuleId=<guid>)` |
| metadata path | `EntityDefinitions(LogicalName='account')/Keys` |

A `?` or `$` in the positional arg is rejected client-side before the request — move
those values onto `--select`, `--filter`, etc.

**Bound function — unquoted GUID parameter (§11):**

```bash
crm --json query odata "RetrieveAppComponents(AppModuleId=00000000-0000-0000-0000-000000000001)"
```

For `Edm.Guid` parameters, embed the GUID directly in the path — quoting it causes the
server to reject it.

**Metadata path — list an entity's lookup relationships:**

```bash
crm --json query odata "EntityDefinitions(LogicalName='account')/ManyToOneRelationships" \
  --select ReferencedEntity,ReferencingAttribute
```

Metadata navigation paths (`/Keys`, `/ManyToOneRelationships`, `/Attributes`, …) are
forwarded verbatim to the Web API.
