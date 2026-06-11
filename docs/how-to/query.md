# How-to: query

Read and verify recipes, taken from the CRMWorx build (§4). See the
[CLI reference](../reference/cli.md) for every flag.

## Run an OData query with a filter and projection

```bash
crm --json query odata cwx_tickets \
  --filter "cwx_priority eq 3" --select cwx_name,cwx_severity --top 10
```
Returns matching rows under `data.value`; queries the entity-set (plural) name.

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

## Strip annotations for token-efficient JSON (`--minimal`)

```bash
crm --json query odata cwx_tickets \
  --filter "cwx_priority eq 3" --select cwx_name,cwx_severity --annotations --minimal
```
In `--json` mode `--minimal` drops every OData annotation key (anything containing `@` — `@odata.etag`, `*@OData.Community.Display.V1.FormattedValue`, `*@…lookuplogicalname`) from each record, keeping business fields, `_*_value` lookup GUIDs, and the primary id; the `value`-list envelope (`@odata.count`/`@odata.nextLink`) is preserved. It is a no-op in human/table mode and also works on `query fetchxml`, `query saved`, `query user`, and `entity get`.

## Run a FetchXML query

```bash
crm --json query fetchxml cwx_tickets --xml '
<fetch top="20">
  <entity name="cwx_ticket">
    <attribute name="cwx_name"/>
    <attribute name="cwx_priority"/>
    <order attribute="cwx_name"/>
  </entity>
</fetch>'
```
FetchXML is the server-side XML query language; the `<entity name>` is the logical name, while the command's entity argument is the entity-set name.

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
crm --json query odata "RetrieveAppComponents(AppModuleId=79bdfbec-725e-f111-b65d-00155d467b90)"
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
