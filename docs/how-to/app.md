# How-to: app

Create model-driven apps (appmodule) and bind components, taken from the CRMWorx build (§11, §13). See the
[CLI reference](../reference/cli.md) for every flag.

## Create the app (idempotent)

```bash
crm --json app create --name CRMWorx --unique-name cwx_crmworx \
  --description "CRMWorx IT ticketing" --if-exists skip
```
`--unique-name` must carry the publisher prefix; `--if-exists skip` reports a skip with the existing `appmoduleid` instead of duplicating — this holds even when a just-created app isn't query-visible yet (on-prem publishes before a new appmodule becomes readable): the server's duplicate fault is treated as a skip, with a best-effort `appmoduleid` that may be `null` until the app is published. The required `webresourceid` icon comes from `--icon-webresource <name|guid>` (a name is resolved to its id, a GUID used directly, e.g. `--icon-webresource cwx_/icons/app.svg`); omit it to keep the platform default icon.

## Bind views, charts, forms, and the dashboard

```bash
crm --json app add-components <appmoduleid> \
  --component view:<savedqueryid> --component chart:<savedqueryvisualizationid> \
  --component form:<formid> --component dashboard:<dashboard-formid>
```
`<appmoduleid>` comes from `app create`. `--component` is repeatable as `kind:guid`; kinds are `view|chart|form|dashboard|sitemap|bpf`. Tables surface through the sitemap, not here.

## Unbind components

```bash
crm --json app remove-components <appmoduleid> \
  --component view:<savedqueryid> --component chart:<savedqueryvisualizationid>
```
The inverse of `add-components` (RemoveAppComponents): same repeatable `kind:guid` grammar and the same `view|chart|form|dashboard|sitemap|bpf` vocabulary. Use `crm --dry-run app remove-components ...` to preview the components it would unbind without issuing the call.

## Attach a sitemap

```bash
crm --json app set-sitemap "CRMWorx Sitemap" --xml-file sitemap.xml --unique-name cwx_crmworx
```
Reads SiteMapXml from `--xml-file`; `--unique-name` sets `sitemapnameunique` so the sitemap auto-associates with that app.

## Build a sitemap from structured input

```bash
crm --json app build-sitemap "CRMWorx Sitemap" \
  --area 'sales:Sales' \
  --group 'sales/accounts:Customers' \
  --subarea 'sales/accounts:entity=account:Accounts' \
  --subarea 'sales/accounts:entity=contact' \
  --unique-name cwx_crmworx
```
Generates the SiteMapXml for you, then creates the sitemap via the same path as `set-sitemap` (which instead uploads a pre-built XML file). The grammar is `--area 'id[:Title]'` (repeatable, at least one required), `--group 'areaId/groupId[:Title]'` (nested under an area), and `--subarea 'areaId/groupId:entity=<logical>[:Title]'`. Titles are optional throughout: omit an Area/Group title and it falls back to its own Id as the label. A SubArea binds a table through the SiteMapXml `Entity=` attribute; its Title is optional too, and when omitted the platform derives the label from the entity. SubArea Ids are auto-allocated from the entity logical name (you don't supply them); Area/Group Ids and the references between them are validated, so broken references or duplicate Ids fail with an error. Every attribute value is XML-escaped. `--unique-name` sets `sitemapnameunique` to auto-associate with that app, exactly as on `set-sitemap`. Creation publishes by default (`--no-publish` to skip). Use `crm --dry-run app build-sitemap ...` to print the generated SiteMapXml without creating anything.

## Delete the app

```bash
crm --dry-run app delete cwx_crmworx          # preview: names the app + dependent rows it would sweep, issues no DELETE
crm app delete cwx_crmworx --yes              # delete, skipping the destructive-op confirmation
```

Resolves `NAME_OR_ID` as an `appmoduleid` (GUID), else by `uniquename`, else by display `name`; an unknown or ambiguous name fails with a clear error. Before deleting the app it **sweeps the dependent data rows that hold a record-level foreign key to it** — chiefly `appsetting`. Without that sweep a bare `crm entity delete appmodules <id>` fails with server error `0x80048d21` ("cannot delete because it is referenced by another record"). This FK block happens on **both** on-prem v9.x and Dataverse online (online blocks too, even though the `appsetting` relationship's metadata claims cascade-delete), so the sweep does not trust cascade metadata — it removes whatever rows reference the app. A **managed** app is refused with an actionable error (uninstall its parent solution instead). The destructive-op gate is `--yes` (skip the confirmation, exactly like `entity delete`); the global `--dry-run` and `--json` apply.
