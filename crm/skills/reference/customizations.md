# Customizations — apps, web resources, ribbon, forms, sitemap

UI-layer customization: model-driven apps and their sitemaps, web resources, entity
command-bar (ribbon) buttons, and entity forms. Groups: `app`, `webresource`,
`ribbon`, `form`. Flags/choices: `crm <group> --help`.

## Model-driven apps — `app` (appmodule)

```bash
# create: --unique-name is publisher-prefixed, e.g. 'cwx_crmworx'.
crm --json app create --name CRMWorx --unique-name cwx_crmworx --if-exists skip

# add-components: APP_ID positional + repeatable --component 'kind:guid'.
# 'entity' is NOT a valid kind — tables surface via sitemap Entity= subareas.
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

**Create→sitemap seam — carry the `appmoduleid`, don't re-create.** `app create`
publishes the app and then reads it back; in the publish-before-read window that
read-back can fail with a `meta.warnings` `app_lookup_error` **even though the app was
created**. The created `appmoduleid` is still in `data` — capture it and feed it to
`add-components`, `build-sitemap`, and teardown. Do **not** re-run `app create`: the app
already exists, a second create with a *new* `--unique-name` orphans a duplicate, and a
retry with the *same* name can hit `0x80050135` (duplicate) because the existence
pre-check rides that same not-yet-published read. Treat `app create` as create-once and
chain off its returned id.

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

**Ribbon writes are slow and synchronous.** Because every write rides the solution-zip
pipeline, `add-button` / `remove` run a **full solution import per call** — 60–120s with
no progress ticks. The command has not hung; **do not retry** a slow call (a second,
parallel attempt races the first import). Confirm the outcome afterward with
`ribbon list`.

## Forms — `form` (entity main forms / systemform)

```bash
crm --json form list cwx_ticket                                 # main forms on a table
crm --json form clone cwx_ticket "Information" --to cwx_ticketclone   # clone a named form to another table
crm --json form export cwx_ticket "Information" --output form.xml     # export a form's formxml
```

### Add a field to an existing form

`form` has no field-editing verb — edit the **FormXml** by hand, the same shape as the
view-edit recipe in `reference/authoring.md`: export, splice the control into the layout,
PATCH the `systemforms` row, then publish the entity.

```bash
crm --json form export cwx_ticket "Information" --output form.xml
# splice a <control> (carrying the field's classid) into a <cell> of the target
# <section>, then PATCH only the formxml back:
crm entity update systemforms <formid> --data-file form-update.json   # {"formxml":"…"}
crm solution publish --xml \
    '<importexportxml><entities><entity>cwx_ticket</entity></entities></importexportxml>'
```

Use `--data-file`, **not** inline `--data` — FormXml is quote-heavy and must be
JSON-escaped. Get `<formid>` from `form list`. A control's `classid` is a D365 platform
constant per control type (stable across orgs) — the common ones:

| control type | `classid` |
|---|---|
| single line of text | `{4273EDBD-AC1D-40d3-9FB2-095C621B552D}` |
| multiline text | `{E0DECE4B-6FC8-4a8f-A065-082708572369}` |
| option set / picklist | `{3EF39988-22BB-4f0b-BBBE-64B5A3748AEE}` |
| lookup | `{270BD3DB-D9AF-4782-9025-509E298DEC0A}` |

For any other type, `form export` a stock table that already carries that control (e.g.
`account`) and copy its `<control>`'s `classid` — don't guess it. After publishing,
**re-export the form and confirm your `<control>` is present**: a malformed splice
publishes without error but silently drops the field.

On Unified Interface a cloned/added form may need adding to the model-driven app's form
list to be visible.

## Decommission — deleting UI components

`app` and `ribbon` have **no `delete` verb** — delete through the generic
`entity delete <set>`, or drop the whole solution to cascade. `webresource` has a
first-class `crm webresource delete <name|id> --yes`. Order matters; the platform
enforces the dependencies and the error code names the one you hit:

- **Web resource referenced by a ribbon button** — remove the button first
  (`ribbon remove …`), else the delete fails `0x8004f01f` (still referenced). Use
  `crm webresource delete <name|id> --check-dependencies` to preview blockers before
  attempting the delete. After clearing the button, retry:
  `crm webresource delete <name|id> --yes`.
- **Model-driven app** — `entity delete appmodules <id>` can fail `0x80048d21` (an
  `appsettings` FK still points at it). Deleting the **containing solution** cascades the
  app away cleanly; reach for the per-record delete only when the app lives outside a
  solution you can drop.
- **Custom table** — `metadata delete-entity <logical> --yes` cascades its columns,
  relationships, views, and forms in one shot; delete the table before the global option
  sets and publisher it depended on.
