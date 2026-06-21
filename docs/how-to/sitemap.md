# How-to: sitemap (live navigation editor)

Edit an **existing** model-driven app SiteMap's navigation tree in place — add or
remove Areas, Groups, and SubAreas — without re-authoring the whole document. See
the [CLI reference](../reference/cli.md) for every flag.

These verbs complement `app build-sitemap` / `app set-sitemap` (which POST a whole
new SiteMapXml). They operate over a **read-modify-write** path: GET the live
`sitemapxml`, mutate the parsed XML tree, PATCH it back, then optionally publish.

## Find the sitemap GUID

All `sitemap` verbs take a `SITEMAP_ID` positional argument — the sitemap record's
GUID. Retrieve it by name:

```bash
crm --json query odata sitemaps --select sitemapname,sitemapid
```

The `sitemapid` in `data[]` is what you pass as `SITEMAP_ID`.

## Add an Area

```bash
crm --json sitemap add-area <SITEMAP_ID> \
    --id cwx_sales --title "Sales" --publish
```

`--id` must match `[a-zA-Z0-9_]+` and be **unique across all node ids** in the
document (every Area / Group / SubArea Id — so `remove-node --id` is never
ambiguous). A publisher prefix (e.g. `cwx_`) is recommended. `--icon` accepts a
path string or
the `$webresource:<name>` directive. `--show-groups` sets `ShowGroups='true'` on the
new Area.

## Add a Group under an Area

```bash
crm --json sitemap add-group <SITEMAP_ID> \
    --area cwx_sales --id cwx_salesgrp --title "Customers" --publish
```

`--area` is the Id of the parent Area (must already exist). The new Group Id must be
**unique across all node ids** in the document.

## Add a SubArea under a Group

A SubArea requires **exactly one** content binding: `--entity`, `--url`, or
`--dashboard`. Passing more than one, or none, is a usage error.

```bash
# Bind a table by logical name (validated to exist in the org)
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_salesgrp \
    --id cwx_accounts --entity account --title "Accounts" --publish

# Link to a URL (including an HTML web resource)
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_salesgrp \
    --id cwx_dashboard --url "/WebResources/cwx_dashboard.html" --publish

# Open a dashboard by GUID (sets DefaultDashboard)
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_salesgrp \
    --id cwx_pipe --dashboard <dashboard-guid> --publish
```

**`--entity` is validated live** — a logical name that does not exist in the org is
rejected before the PATCH, because a dangling `Entity=` silently hides the SubArea
in the UI.

**There is no SubArea `WebResource` attribute.** A web-resource-backed SubArea uses
`--url` pointing at the web resource URL. The `$webresource:` prefix is the `--icon`
directive only.

The new SubArea Id must be **unique across all node ids** in the document.

## Remove (or comment out) a node

```bash
# Hard delete — removes the node and its descendants
crm --json sitemap remove-node <SITEMAP_ID> --id cwx_accounts --publish

# Soft delete — replaces the node with a well-formed XML comment
crm --json sitemap remove-node <SITEMAP_ID> --id cwx_accounts --comment-out --publish
```

`remove-node` warns when the target is an Area or Group that has descendants
(cascade warning surfaced in `meta.warnings`). The command proceeds — pass
`--dry-run` first to preview exactly which subtree would be swept.

## Publish-gated read-back — and why not to chain `--no-publish` edits

> **Gotcha — `sitemapxml` reads/writes go through the published layer.** A Web
> API GET for `sitemapxml` returns the *last published* snapshot, not a staged
> edit (on on-prem v9.x especially). An edit written with `--no-publish` does not
> appear in a re-fetch until `PublishAllXml` runs.

This has a sharp consequence for **multiple edits to the same sitemap**: because
each verb reads `sitemapxml` fresh before mutating, a second `--no-publish` edit
re-reads the *published* layer (without the first edit) and PATCHes over it —
**silently discarding the first unpublished edit.** So do **not** chain
`--no-publish` edits against one sitemap.

Instead, let each edit publish before the next reads — `--publish` is the default
(it runs `PublishAllXml` and a T3 read-back inside the verb), so plain sequential
commands are safe:

```bash
crm --json sitemap add-area <SITEMAP_ID> --id cwx_ops --title "Operations"
crm --json sitemap add-group <SITEMAP_ID> --area cwx_ops --id cwx_opsgrp \
    --title "Ops Group"
crm --json sitemap add-subarea <SITEMAP_ID> --area cwx_ops --group cwx_opsgrp \
    --id cwx_contacts --entity contact
```

Reserve `--no-publish` for a **single** staged edit you publish yourself (e.g.
`crm solution publish-all`) — not for batching several edits to the same sitemap.

## Solution scoping

```bash
crm --json sitemap add-area <SITEMAP_ID> \
    --id cwx_ops --title "Operations" \
    --solution cwx_crmworx --publish
```

`--require-solution` fails the command if no solution name resolves from `--solution`
or the profile default.

## Preview without writing

The global `--dry-run` flag reads the live sitemap for real (to validate parent
references, check uniqueness, and resolve entities) but issues no PATCH:

```bash
crm --json --dry-run sitemap add-area <SITEMAP_ID> --id cwx_ops --title "Operations"
```
