# How-to: app

Create model-driven apps (appmodule) and bind components, taken from the CRMWorx build (§11, §13). See the
[CLI reference](../reference/cli.md) for every flag.

## Create the app (idempotent)

```bash
crm --json app create --name CRMWorx --unique-name cwx_crmworx \
  --description "CRMWorx IT ticketing" --if-exists skip
```
`--unique-name` must carry the publisher prefix; `--if-exists skip` reports a skip with the existing `appmoduleid` instead of duplicating. `create` always sets the required `webresourceid` to the platform default icon.

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
