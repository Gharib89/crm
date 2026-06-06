# How-to: query

Read and verify recipes, taken from the CRMWorx build (§4). See the
[CLI reference](../reference/cli.md) for every flag.

## Run an OData query with a filter and projection

```bash
crm --json query odata cwx_tickets \
  --filter "cwx_priority eq 3" --select cwx_name,cwx_severity --top 10
```
Returns matching rows under `data.value`; queries the entity-set (plural) name.

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

## Call a bound function on the URL path (unquoted GUID)

```bash
crm --json query odata "RetrieveAppComponents(AppModuleId=79bdfbec-725e-f111-b65d-00155d467b90)"
```
For functions whose parameter is an `Edm.Guid`, embed it in the URL path so it stays unquoted — `--params` would quote it and the server rejects that (§11).
