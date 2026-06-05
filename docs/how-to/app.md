# How-to: app

Create model-driven apps (appmodule) and bind components, taken from the CRMWorx build (§11, §13). See the
[CLI reference](../reference/cli.md) for every flag.

## Create the app (idempotent)

```bash
crm --json app create --name CRMWorx --unique-name cwx_crmworx \
  --description "CRMWorx IT ticketing" --if-exists skip
```
`--unique-name` must carry the publisher prefix; `--if-exists skip` reports a skip with the existing `appmoduleid` instead of duplicating. The required `webresourceid` icon comes from `--icon-webresource <name|guid>` (a name is resolved to its id, a GUID used directly, e.g. `--icon-webresource cwx_/icons/app.svg`); omit it to keep the platform default icon.

## Bind views, charts, forms, and the dashboard

```bash
crm --json app add-components <appmoduleid> \
  --component view:<savedqueryid> --component chart:<savedqueryvisualizationid> \
  --component form:<formid> --component dashboard:<dashboard-formid>
```
`<appmoduleid>` comes from `app create`. `--component` is repeatable as `kind:guid`; kinds are `view|chart|form|dashboard|sitemap|bpf`. Tables surface through the sitemap, not here.

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
