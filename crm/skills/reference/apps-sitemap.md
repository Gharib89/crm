# Model-driven apps & sitemap — `app`, `sitemap`

Create model-driven apps, bind their components, and author or live-edit the
navigation tree. Groups: `app` (appmodule), `sitemap`. Flags/choices:
`crm <group> --help`. Mutating verbs are solution-scoped and stage by default
(see the SKILL.md agent contract); per-verb exceptions are noted below.

## Model-driven apps — `app` (appmodule)

`app create`, `build-sitemap`, and `set-sitemap` are solution-scoped;
`add-components`/`remove-components` bind existing ids and take no `--solution`.

```bash
# create: --unique-name is publisher-prefixed, e.g. 'contoso_salesapp'.
crm --json app create --name "Contoso Sales" --unique-name contoso_salesapp --solution ContosoCore --if-exists skip

# add-components: APP_ID positional + repeatable --component 'kind:guid'.
# 'entity' is NOT a valid kind — tables surface via sitemap Entity= subareas.
crm --json app add-components <appmoduleid> \
    --component view:<savedqueryid> --component chart:<savedqueryvisualizationid>

# remove-components: inverse of add-components (RemoveAppComponents), same
# 'kind:guid' grammar + same vocabulary. --dry-run previews without calling.
crm --json app remove-components <appmoduleid> --component view:<savedqueryid>

# set-sitemap: SITEMAP_NAME positional is the sitemap's descriptive name
# (stored as sitemapname); --unique-name is the app's uniquename and sets
# sitemapnameunique to auto-associate the sitemap with that app.
crm --json app set-sitemap "Contoso Sales Sitemap" --xml-file /tmp/sitemap.xml \
    --unique-name contoso_salesapp --solution ContosoCore

# build-sitemap: generates the SiteMapXml for you, then creates it via the same
# path as set-sitemap. Grammar: --area 'id[:Title]', --group 'areaId/groupId[:Title]',
# --subarea 'areaId/groupId:entity=<logical>[:Title]' (binds a table via Entity=).
# SubArea Ids are auto-allocated; refs/dup Ids are validated.
# crm --dry-run app build-sitemap ... prints the generated XML and does NOT POST.
crm --json app build-sitemap "Contoso Sales Sitemap" \
    --area 'sales:Sales' --group 'sales/accounts:Customers' \
    --subarea 'sales/accounts:entity=account:Accounts' \
    --subarea 'sales/accounts:entity=contact' --unique-name contoso_salesapp --solution ContosoCore
```

**On Unified Interface, tables are NOT added via `add-components`** — they surface
through the sitemap's `Entity=` subareas. A newly created entity is invisible in an
app until a subarea references it.

**Create→sitemap seam — carry the `appmoduleid`, don't re-create.** `app create`
**stages** by default and then reads the new app back; on on-prem especially, an
unpublished appmodule isn't query-visible yet, so that read-back commonly fails
with a `meta.warnings` `app_lookup_error` **even though the app was created** —
pass `--publish` to publish before the read-back and avoid it. The created
`appmoduleid` is still in `data` either way — capture it and feed it to
`add-components`, `build-sitemap`, and teardown. Do **not** re-run `app create`: the app
already exists, a second create with a *new* `--unique-name` orphans a duplicate, and a
retry with the *same* name can hit `0x80050135` (duplicate) because the existence
pre-check rides that same not-yet-published read. Treat `app create` as create-once and
chain off its returned id.

**Teardown — use `app delete <name|id>`, not `entity delete appmodules`.** An app
won't delete while a dependent row holds a record-level FK to it: a bare
`entity delete appmodules <id>` fails `0x80048d21` ("referenced by another record"),
chiefly because an `appsetting` row still points at it. This block hits on **both**
on-prem and online — online too, despite the `appsetting` relationship's cascade-delete
metadata. `app delete` resolves the app (GUID / uniquename / display name), sweeps those
FK-blocking dependent rows first, then deletes the app; its `data` lists every dependent
removed (real run `dependents_deleted: [{entity, id}]`; `--dry-run` previews them under
`would_delete.dependents` and issues no DELETE). It **refuses a managed app** — uninstall
the parent solution instead.

## SiteMap live editor — `sitemap`

Surgically edit an **existing** sitemap's navigation tree in place over the
read-modify-write (RMW) seam: GET `sitemaps({id})?$select=sitemapxml` → mutate the
parsed XML tree → PATCH → publish → read-back. Complements `app build-sitemap` /
`app set-sitemap` which POST a whole new SiteMapXml. Because every verb re-reads
the *published* `sitemapxml` before mutating, the staged-writes rule (SKILL.md)
bites hardest here — a second staged edit silently discards the first; `--publish`
runs `PublishAllXml` + a read-back inside the verb itself.

**Find the sitemap GUID first:**

```bash
crm --json query odata sitemaps --select sitemapname,sitemapid
# → data[].sitemapid is the SITEMAP_ID positional arg
```

**The seven verbs — all solution-scoped:**

```bash
# Add an Area (id unique across all node ids; publisher-prefix recommended)
crm --json sitemap add-area <SITEMAP_ID> --id contoso_sales --title "Sales" --solution ContosoCore --publish

# Add a Group under an Area
crm --json sitemap add-group <SITEMAP_ID> \
    --area contoso_sales --id contoso_grp --title "Customers" --solution ContosoCore --publish

# Add a SubArea — exactly one of --entity / --url / --dashboard
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area contoso_sales --group contoso_grp --id contoso_accts --entity account --solution ContosoCore --publish
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area contoso_sales --group contoso_grp --id contoso_page --url "/WebResources/contoso_.html" --solution ContosoCore --publish
crm --json sitemap add-subarea <SITEMAP_ID> \
    --area contoso_sales --group contoso_grp --id contoso_dash --dashboard <guid> --solution ContosoCore --publish

# Reorder a node within its parent — exactly one of --before / --after / --index
crm --json sitemap move-node <SITEMAP_ID> --id contoso_accts --before contoso_dash --solution ContosoCore --publish
crm --json sitemap move-node <SITEMAP_ID> --id contoso_accts --after contoso_dash --solution ContosoCore --publish
crm --json sitemap move-node <SITEMAP_ID> --id contoso_accts --index 0 --solution ContosoCore --publish

# Remove (or soft-delete with --comment-out)
crm --json sitemap remove-node <SITEMAP_ID> --id contoso_accts --solution ContosoCore --publish
crm --json sitemap remove-node <SITEMAP_ID> --id contoso_sales --comment-out --solution ContosoCore --publish

# Set localized titles — --lcid/--title paired positionally, repeatable
crm --json sitemap set-title <SITEMAP_ID> \
    --id contoso_sales --lcid 1033 --title "Sales" --lcid 1031 --title "Vertrieb" --solution ContosoCore --publish

# Set localized descriptions — same shape as set-title
crm --json sitemap set-description <SITEMAP_ID> \
    --id contoso_sales --lcid 1033 --description "Sales area" --solution ContosoCore --publish
```

**Workflow-level gotchas the `--help` doesn't surface:**

- **Exactly one content binding per SubArea.** `--entity`, `--url`, and `--dashboard`
  are mutually exclusive. Passing more than one, or none, is a usage error.
- **`--entity` is validated live.** A logical name that doesn't exist in the org is
  rejected before the PATCH — a dangling `Entity=` would silently hide the SubArea.
- **`--dashboard` is validated live.** The GUID must resolve to an existing
  `systemform` with `type == 0`. A well-formed but nonexistent GUID → "no dashboard
  with id … exists"; a GUID that points to a non-dashboard systemform (e.g. an entity
  form) → "not a dashboard". A dangling `DefaultDashboard` renders a broken tile at
  runtime.
- **`--pass-params` is only valid with `--url`.** Emits `PassParams="true"` on the
  new `<SubArea>` so Dynamics appends context parameters (`userid`, `orgname`,
  `orglcid`, `userlcid`) to the navigated URL. Combining it with `--entity` or
  `--dashboard` is a usage error (exit 2).
- **There is no SubArea `WebResource` attribute.** A web-resource-backed SubArea uses
  `--url` (pointing at the web resource URL path). The `$webresource:` prefix is
  the `--icon` directive only, not a content binding.
- **`ResourceId` and `IntroducedVersion` are never written.** These are
  platform-owned — new nodes get only `Title`; the CLI never touches them.
- **Every new node Id is unique across the whole document** (all Area / Group /
  SubArea Ids), matching `build_sitemapxml` — this keeps `remove-node --id`
  unambiguous, since it targets by Id across all node types.
- **`move-node` anchor must share parent and node type.** The `--before` / `--after`
  sibling must be in the same parent container and be the same node type (Area/Group/
  SubArea) as the moved node. `--index` must be in range. Any mismatch or
  out-of-range value is a clear error with no write. `move-node` is a pure
  permutation — it never modifies the node's attributes or children.
- **`remove-node` cascades** — removing an Area or Group that has descendants emits a
  `meta.warnings` cascade advisory. Use `--dry-run` first to preview the subtree.
- **`--comment-out`** replaces the node with a well-formed XML comment instead of
  deleting it — a reversible soft-delete. The commented node is not a live node, so
  its id frees up for reuse (uniqueness checks scan live nodes only).
- **`set-title` / `set-description` — `--lcid` must be provisioned.** Before the
  PATCH, the CLI calls `RetrieveProvisionedLanguages` and rejects any LCID not
  installed on the org. A title/description for an un-provisioned language is silently
  ignored by the platform, so the rejection is intentional — install the language pack
  first.
- **One Title/Description per LCID — updates in place, never duplicates.** Re-setting
  the same LCID replaces the existing element. Malformed CLI input — duplicate or
  non-4-digit `--lcid`, blank text, blank `--id`, mismatched `--lcid`/value counts —
  is a Click usage error (exit 2), validated before any backend call. A well-formed
  but **un-provisioned** LCID needs the live check, so it surfaces through the normal
  error envelope (exit 1).
- **Strict child-element ordering within a node.** The XSD requires `<Titles>` before
  `<Descriptions>` before child nodes (Group/SubArea). A new container is spliced into
  the correct position — never appended after child nodes, which would be
  schema-invalid and fail on import.
- **`ResourceId` on Title/Description elements is never written.** The
  platform-owned localized-label pointer is left intact; only the inline text
  attribute is set.

**JSON contract — same envelope as all customization verbs:**

```json
{ "ok": true,
  "data": {"sitemapid": "…", "action": "add-area", "area_id": "contoso_sales",
           "title": "Sales", "updated": true, "published": true},
  "meta": {} }
```

`data` carries the edit's identifying fields (`action`, plus `area_id` /
`group_id` / `sub_id` / `node_id` per verb). `meta.warnings` carries the cascade
advisory (Area/Group with descendants removed). `--dry-run` returns
`{_dry_run: true, would_edit: true, sitemapxml: "<…>"}` — parent/entity validation
reads still run; no PATCH is issued.
