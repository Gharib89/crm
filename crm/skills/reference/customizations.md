# Customizations — apps, web resources, ribbon, forms, sitemap

UI-layer customization: model-driven apps and their sitemaps, web resources, entity
command-bar (ribbon) buttons, and entity forms. Groups: `app`, `webresource`,
`ribbon`, `form`. Flags/choices: `crm <group> --help`.

## Model-driven apps — `app` (appmodule)

```bash
# create: --unique-name is publisher-prefixed, e.g. 'cwx_crmworx'.
crm --json app create --name CRMWorx --unique-name cwx_crmworx --if-exists skip

# add-components: APP_ID positional + repeatable --component 'kind:guid'.
# kind ∈ view|chart|form|dashboard|sitemap|bpf (NOT 'entity' — tables surface
# via the sitemap's Entity= subareas, not AddAppComponents).
crm --json app add-components <appmoduleid> \
    --component view:<savedqueryid> --component chart:<savedqueryvisualizationid>

# set-sitemap: SITEMAP_NAME positional is the sitemap's descriptive name
# (stored as sitemapname); --unique-name is the app's uniquename and sets
# sitemapnameunique to auto-associate the sitemap with that app.
crm --json app set-sitemap "CRMWorx Sitemap" --xml-file /tmp/sitemap.xml --unique-name cwx_crmworx

# build-sitemap: generates the SiteMapXml for you, then creates it via the same
# path as set-sitemap. Grammar: --area 'id[:Title]', --group 'areaId/groupId[:Title]',
# --subarea 'areaId/groupId:entity=<logical>[:Title]' (binds a table via Entity=).
# SubArea Ids are auto-allocated; refs/dup Ids are validated.
# crm --dry-run app build-sitemap ... prints the generated XML and does NOT POST.
crm --json app build-sitemap "CRMWorx Sitemap" \
    --area 'sales:Sales' --group 'sales/accounts:Customers' \
    --subarea 'sales/accounts:entity=account:Accounts' \
    --subarea 'sales/accounts:entity=contact' --unique-name cwx_crmworx
```

**On Unified Interface, tables are NOT added via `add-components`** — they surface
through the sitemap's `Entity=` subareas. A newly created entity is invisible in an
app until a subarea references it.

## Web resources — `webresource` (HTML/JS/CSS/images)

```bash
# create: --file bytes are base64'd into `content`; webresourcetype is inferred from
# the extension (the real D365 option set, so .css=2 and 8 is Silverlight). An unknown
# extension without an explicit --type is rejected.
crm --json webresource create --name cwx_/scripts/ribbon.js --file ./ribbon.js --solution cwx_crmworx

# update <name>: plain PATCH of only the sent fields (content and/or display-name),
# resolved by name — NOT retrieve-merge.
crm --json webresource update cwx_/scripts/ribbon.js --file ./ribbon.js

# inspect
crm --json webresource get cwx_/scripts/ribbon.js
crm --json webresource list --custom-only

# use as a model-driven app icon
crm --json webresource create --name cwx_/icons/app.svg --file ./app.svg
crm --json app create --name CRMWorx --unique-name cwx_crmworx --icon-webresource cwx_/icons/app.svg
```

Both `create` and `update` honor `--solution` (`MSCRM.SolutionUniqueName`) and publish
after the write (`--no-publish` / global `--stage-only` suppress it; see
`reference/authoring.md`).

## Ribbon — `ribbon` (entity command-bar buttons)

The ribbon is stored as `RibbonDiffXml` and has **no first-class Web API write path**:
this group drives it through a solution zip + XML pipeline, so **every subcommand
except `export` works through the solution-zip pipeline — all require `--solution`.**
A button that runs a web resource needs that **web resource to already exist** —
create it first (above).

```bash
crm --json ribbon export account                 # read current RibbonDiffXml
crm --json ribbon list account --solution cwx_crmworx
crm --json ribbon add-button account --solution cwx_crmworx ...
crm --json ribbon remove account --solution cwx_crmworx ...
```

This is why a cloned entity's ribbon does not come across (see the clone caveats in
`reference/metadata.md`) — there is no API write path to copy it.

## Forms — `form` (entity main forms / systemform)

```bash
crm --json form list cwx_ticket                                 # main forms on a table
crm --json form clone cwx_ticket "Information" --to cwx_ticketclone   # clone a named form to another table
crm --json form export cwx_ticket "Information" --output form.xml     # export a form's formxml
```

On Unified Interface a cloned/added form may need adding to the model-driven app's form
list to be visible.
