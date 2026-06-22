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

# Link to a URL and append Dynamics context params (userid, orgname, orglcid, userlcid)
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_salesgrp \
    --id cwx_ext --url "https://portal.example.com/widget" --pass-params --publish

# Open a dashboard by GUID (sets DefaultDashboard)
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area cwx_sales --group cwx_salesgrp \
    --id cwx_pipe --dashboard <dashboard-guid> --publish
```

**`--entity` is validated live** — a logical name that does not exist in the org is
rejected before the PATCH, because a dangling `Entity=` silently hides the SubArea
in the UI.

**`--dashboard` is validated live** — the GUID must resolve to an existing
`systemform` with `type == 0` (a dashboard). A well-formed GUID that doesn't exist
raises "no dashboard with id … exists"; a GUID of a non-dashboard systemform (e.g.
an entity form, `type != 0`) raises "not a dashboard". A dangling `DefaultDashboard`
renders a broken tile at runtime.

**`--pass-params` is only valid with `--url`.** It emits `PassParams="true"` on the
new `<SubArea>`, which tells Dynamics to append context parameters (`userid`,
`orgname`, `orglcid`, `userlcid`) to the navigated URL. Combining it with `--entity`
or `--dashboard` is a usage error (exit 2).

**There is no SubArea `WebResource` attribute.** A web-resource-backed SubArea uses
`--url` pointing at the web resource URL. The `$webresource:` prefix is the `--icon`
directive only.

The new SubArea Id must be **unique across all node ids** in the document.

## Move (reorder) a node

Reorder an existing Area, Group, or SubArea within its parent without touching its
attributes or children. Exactly one destination mode is required:

```bash
# Move directly before a sibling (same parent and node type)
crm --json sitemap move-node <SITEMAP_ID> --id cwx_accounts --before cwx_contacts --publish

# Move directly after a sibling
crm --json sitemap move-node <SITEMAP_ID> --id cwx_accounts --after cwx_contacts --publish

# Move to a 0-based position among same-type siblings
crm --json sitemap move-node <SITEMAP_ID> --id cwx_accounts --index 0 --publish
```

The anchor for `--before` / `--after` must share the moved node's **parent and node
type** (e.g. you cannot anchor a Group move on an Area Id). `--index` must be in
range. A mismatch or out-of-range index is a clear error — no write is issued.

`move-node` is a pure permutation: it only repositions the node; its attributes,
children, and descendant structure are never modified.

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

## Set localized titles on a node

`set-title` writes `<Title LCID="…" Title="…"/>` elements inside a `<Titles>` block
on an Area, Group, or SubArea. Pass `--lcid` and `--title` as a pair; both flags are
repeatable and paired positionally (first `--lcid` matches first `--title`, and so
on).

```bash
# Set a single English title on a node
crm --json sitemap set-title <SITEMAP_ID> \
    --id cwx_sales --lcid 1033 --title "Sales" --publish

# Set titles for multiple languages in one call
crm --json sitemap set-title <SITEMAP_ID> \
    --id cwx_sales \
    --lcid 1033 --title "Sales" \
    --lcid 1031 --title "Vertrieb" \
    --publish
```

**`--lcid` must name an installed language.** Before the PATCH, the CLI calls
`RetrieveProvisionedLanguages` and rejects any LCID that is not provisioned on the
org. A `<Title>` for an un-provisioned language is silently ignored by the platform,
so the rejection is intentional — install the language pack first.

**One Title per LCID — updates in place.** If a `<Title>` for that LCID already
exists, it is updated rather than duplicated. The XSD permits duplicate LCIDs, but
the CLI enforces uniqueness: passing the same `--lcid` twice in a single call is a
usage error (exit 2), as is a non-4-digit `--lcid`, a blank `--title`, a blank
`--id`, or a mismatched count of `--lcid` and `--title` flags — all validated
up front before any server call. (An LCID that is well-formed but **not an
installed language** is caught by the live check and reported through the normal
error envelope, exit 1.)

**`ResourceId` is never touched.** The platform-owned localized-label pointer is
left intact; only the inline `Title=` attribute of the `<Title>` element is written.

**Strict child-element ordering is preserved.** Within a node, the XSD requires
`<Titles>` before `<Descriptions>` before child nodes (Group/SubArea). When the CLI
splices in a new `<Titles>` container it inserts it at the correct position — never
after child nodes, which would be schema-invalid and fail on import.

The JSON response echoes `action`, `node_id`, and `titles` as a list of
`{lcid, title}` objects:

```json
{ "ok": true,
  "data": {"sitemapid": "…", "action": "set-title", "node_id": "cwx_sales",
           "titles": [{"lcid": 1033, "title": "Sales"}, {"lcid": 1031, "title": "Vertrieb"}],
           "updated": true, "published": true},
  "meta": {} }
```

## Set localized descriptions on a node

`set-description` writes `<Description LCID="…" Description="…"/>` elements inside a
`<Descriptions>` block. The shape is identical to `set-title` — `--lcid` and
`--description` are paired positionally and repeatable.

```bash
# Set a single English description
crm --json sitemap set-description <SITEMAP_ID> \
    --id cwx_sales --lcid 1033 --description "Sales area" --publish

# Set descriptions for multiple languages
crm --json sitemap set-description <SITEMAP_ID> \
    --id cwx_sales \
    --lcid 1033 --description "Sales area" \
    --lcid 1031 --description "Vertriebsbereich" \
    --publish
```

All the same rules apply as for `set-title`: malformed input (duplicate or
non-4-digit `--lcid`, blank `--description`/`--id`, mismatched counts) is a usage
error (exit 2) validated before any server call; a well-formed but un-provisioned
`--lcid` is rejected by the live check (exit 1); `ResourceId` is untouched; and
strict `<Titles>` → `<Descriptions>` → child-node ordering is respected.

The JSON response echoes `action`, `node_id`, and `descriptions` as a list of
`{lcid, description}` objects:

```json
{ "ok": true,
  "data": {"sitemapid": "…", "action": "set-description", "node_id": "cwx_sales",
           "descriptions": [{"lcid": 1033, "description": "Sales area"}],
           "updated": true, "published": true},
  "meta": {} }
```

> **Note on the legacy `Title=`/`Description=` attributes.** The SiteMap XSD
> deprecates the plain `Title` and `Description` attributes on nodes in favour of
> the `<Titles>`/`<Descriptions>` child elements. `add-area`, `add-group`, and
> `add-subarea` still write the legacy `Title` attribute — that is unchanged. Use
> `set-title` / `set-description` when you need true per-LCID localisation.

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
