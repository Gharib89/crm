# Inventory manifest v3

A **migration inventory** is one extraction's descriptive record of an org's
customizations ([ADR 0032](../adr/0032-inventory-descriptive-contract-keyed-by-component-key.md)).
This page defines its format, `schema_version` 3, which the CRM 2011 path and
the v9 on-prem and cloud paths all emit. `crm inventory extract` writes one
on any profile: a `crm2011` profile over SOAP, a v9 on-prem or cloud profile
over the Web API ([How-to: inventory](../how-to/inventory.md)).

The format ships in the `crm` wheel as a JSON Schema (draft 2020-12),
`crm/schemas/inventory-manifest-v3.schema.json` (the standalone binaries leave
it out, since no command loads it; the v1 and v2 schemas stay beside it for old manifests), and as typed records with a
reader and writer in `crm/core/inventory.py` (`read_inventory`,
`parse_manifest`, `dump_manifest`, `SCHEMA_VERSION`). The reader checks the
contract without `jsonschema`, and it also checks the rules the schema cannot
express (marked "reader" below).

## Layout

```text
<inventory>/
  manifest.json
  artifacts/<kind>/<slug>-<hash>.<name>
```

`manifest.json` is UTF-8 JSON. Each raw artifact sits directly under
`artifacts/<kind>/`. Its file name is a readable slug of the component key, the
first 12 hex digits of a SHA-256 over the kind and the full key, and the
artifact's name within the record (`form.xml`, `fetch.xml`, `layout.xml`,
`content.js`, `workflow.xaml`). Two keys never share a file, even when they
slug alike. Records that share an ambiguous key add their source id to the hash
(`artifact_path(..., discriminator=<id>)`), or, with no source id (a ribbon), their
key fields' values as JSON, so each keeps its own file. The
reader refuses an artifact path that two records share.

## Header

| Field | Value |
|---|---|
| `schema_version` | `3` (the reader also takes `1` and `2`, see [Versioning](#versioning)) |
| `source.platform` | `crm2011`, `onprem` or `cloud` |
| `source.org_url` | the org's `http` or `https` URL, never with credentials in it |
| `source.organization_id` | the organization id, or `null` |
| `source.version` | the org's build or version string |
| `extracted_at` | when the extraction ran, an RFC 3339 date-time (`2026-11-04T10:00:00Z`); the only time field |
| `cli_version` | the `crm` version that wrote the manifest |
| `scope` | `{"mode": "org", "solutions": []}`, or `{"mode": "solutions", "solutions": [<unique name>...]}`; plus `"system": "excluded"` when stock components were left out (the default org-wide run). Absent or `"included"` means a full read (`--include-system`, or `--solution`). `"usage_read": true` when the run was made with `--with-usage` (absent means no usage was read) |
| `publication` | `published`: extractors read the published customization only and never publish |

## Records

`records` holds one entry per component:

| Field | Value |
|---|---|
| `kind` | one of the kinds below |
| `key` | the component key, which decides matching across inventories |
| `id` | the source GUID, or `null`; informational, it never decides a match |
| `display_name` | the display name, or `null` |
| `origin` | `custom`, `system` or `unknown` |
| `managed` | `true`, `false` or `null` |
| `publication_state` | `published` or `unknown` |
| `fields` | the kind's normalized fields; the key fields below are required, others are the extractor's |
| `artifacts` | `[{path, content, sha256, normalized_sha256}]`, `content` being `text` or `binary` |
| `usage` | optional, written by `extract --with-usage` on `entity`, `plugin_assembly`, `plugin_type`, `step` and `process` records: `{state, signal, value, as_of, reason}`. `state` is `used`, `unused` or `unknown`; `signal` names what was measured; `value` is an integer or `null`; `as_of` is when it was read; `reason` says why a state is `unknown`. A record with no `usage` was not read, which is not the same as `state: unknown`. A row is never `unused` because a record was absent: the probe failing or not answering for it gives `unknown` |

Two records with the same kind and key are **ambiguous**: both stay in the
manifest, and `Inventory.ambiguous()` reports them. Nothing picks one.

## Kinds and component keys

The key joins the kind's key fields, in the order below, with `|`. Each part
escapes `%` as `%25` and `|` as `%7C`, so different field values never give
one key. The code fields (`form_type`, `stage`, `mode`, `image_type`,
`category`, `dependency_type`) are the platform's integer codes, written in
decimal, which CRM 2011 and v9 share. Every other key field is a non-empty
string, and only the four fields the Notes column calls nullable may be
`null`, written as an empty part. The one other empty value is a reference to the
application ribbon or default sitemap (below). A
field holding another component's key (`plugin_type`, `step`, `member_key`,
`dependent_key`, `required_key`) nests it as one escaped part. The reader
refuses a record whose `key` differs from the key its fields give
(`component_key(kind, fields)`).

| Kind | Key fields | Notes |
|---|---|---|
| `entity` | `logical_name` | |
| `attribute` | `entity`, `logical_name` | |
| `relationship` | `schema_name` | |
| `global_option_set` | `name` | |
| `local_option_set` | `entity`, `attribute` | |
| `form` | `entity`, `form_type`, `name` | `form_type` is the `systemform.type` code (2 main, 5 mobile, 7 quick create, ...) |
| `view` | `entity`, `name` | can repeat, so watch for ambiguity |
| `web_resource` | `name` | |
| `plugin_assembly` | `name` | |
| `plugin_type` | `assembly`, `type_name` | one class name can be registered from two assemblies |
| `step` | `message`, `primary_entity`, `stage`, `mode`, `plugin_type`, `name` | `primary_entity` is `null` for a message with no entity |
| `step_image` | `step`, `alias`, `image_type` | |
| `process` | `category`, `primary_entity`, `name` | `category` is the `workflow.category` code; `primary_entity` is nullable |
| `solution` | `unique_name` | |
| `solution_membership` | `solution`, `member_kind`, `member_key` | `member_key` is `""` when `member_kind` is `ribbon` or `sitemap` and the member is the application ribbon or default sitemap |
| `dependency` | `dependent_kind`, `dependent_key`, `required_kind`, `required_key`, `dependency_type` | a key field is `""` on the same terms: its kind field is `ribbon` or `sitemap` and the component is the application ribbon or default sitemap |
| `ribbon` | `entity` | `null` is the application ribbon, key `""` (empty): no entity logical name is empty, so it never meets the ribbon of an entity named `application` |
| `ribbon_diff` | `entity` | the raw `RibbonDiffXml` of one entity (its logical name) or, with `null` and key `""` (empty), of the application: what the org's customizations layer over the stock ribbon, read from a solution export. Only a diff with content is a record (an empty skeleton or `<RibbonNotSupported/>` is not). Never stock. Artifact `ribbon-diff.xml`; see [Ribbon diffs](#ribbon-diffs) |
| `sitemap` | `unique_name` | `null` is the default sitemap, key `""` (empty), for the same reason: a sitemap named `default` keeps its own key |

### Fields beyond the key

`fields` may carry more normalized fields; a reader ignores the ones it does not
know. v1 defines three, so the source-map and packages slices can rely on them,
and the reader and the schema both check their shape when present:

| Kind | Field | Value |
|---|---|---|
| `form` | `libraries` | web resource names of the form libraries, in load order |
| `form` | `handlers` | `[{event, attribute, function, library, enabled}]`, `attribute` `null` for a form-level event |
| `process` | `activities` | plug-in type keys of the custom workflow activities the definition uses |

### Attribute facets

An `attribute` record also carries the facets its type defines (the table's Types
column), so a column can be rebuilt as the source has it. Each is optional: another
type omits it even where the metadata carries a value, and the same keys and units come
from CRM 2011 and from v9 and Dataverse online. The schema types them; a reader ignores
any it does not know, so adding them needed no `schema_version` bump.

| Field | Value | Types |
|---|---|---|
| `max_length` | integer | String, Memo |
| `format` | the type's `Format` enum name (`Text`, `TextArea`, `DateOnly`, ...) | String, Memo, Integer, DateTime |
| `default_value` | a boolean, or a picklist's `DefaultFormValue` integer (`-1` is no default) | Boolean, Picklist |
| `precision` | integer | Decimal, Money, Double |
| `min_value`, `max_value` | number; a whole value is an integer on both platforms | Decimal, Money, Integer, Double |
| `option_set_global` | boolean: whether the option set is a global one | Picklist |

A source that returns nothing for a facet leaves it out; there is no `null`.
`inventory diff` compares a facet only where **both** manifests recorded it, so
a manifest extracted before they existed does not read every column as changed. Both
sides carrying it and differing is a `fields.<name>` change, `fields.max_length`
for a changed length.

### Form activation

A v9 or Dataverse extract records whether a `form` is active as the boolean
`fields.active`, read from `systemform.formactivationstate`. A source that does not
return the column (a CRM 2011 extract, one written before this field existed, or a row
with a null value) records none: the form's activation is not read. The field is optional,
so adding it needed no `schema_version` bump, and `inventory diff` skips it where only one
side recorded it, so a missing value is never a difference or agreement. Both sides
carrying it and differing is a `fields.active` change.

### Entity set name

A v9 or Dataverse extract records the server's `EntitySetName` as `entity_set_name`
on each `entity` record (the Web API collection name, `accounts` for `account`); an
entity whose set name the server returns empty records none. A CRM 2011 extract
records none: the platform has no Web API. `inventory convert --target-inventory`
reads it to resolve an OData v2 call and never derives a set name by pluralising. The
field is optional and free-form, so adding it needed no `schema_version` bump, and
`inventory diff` skips it where only one side recorded it, so an older extract does
not read every entity as changed. Both sides carrying it and differing is a
`fields.entity_set_name` change.

## Kind coverage

`coverage` maps each kind to `{status, pages, records, truncated, errors}`.
`status` is `complete`, `partial`, `unsupported` or `failed`. Rules:

- A truncated read is never `complete` (schema and reader).
- Every kind that has a record has a coverage entry (reader).
- `records` equals the number of records of that kind in the manifest (reader).

Equal counts never prove two orgs equivalent: compare coverage first.

## Extraction gaps

`gaps` lists what the source cannot expose, as `{kind, key, description,
reason}`. `kind` may be `null`, and each entry names a `key` or a
`description`. A `ribbon`, `ribbon_diff` or `sitemap` gap names the application ribbon, the application
ribbon diff or the default sitemap with the key `""`; no other kind may. Typical gaps are an assembly registered on disk or in the GAC,
an ISV page, an external integration, and SQL report data sources.

## Normalization

- A text artifact's `normalized_sha256` hashes its bytes with a leading UTF-8
  BOM stripped and CRLF converted to LF. `sha256` always hashes the raw bytes.
- A binary artifact uses the raw hash for both (reader).
- A ribbon artifact's `normalized_sha256` hashes a canonical form of its XML
  instead: the children of `EnableRules`, `DisplayRules` and
  `CommandDefinitions` are put in a fixed order (by tag, `Id`, then full
  canonical content, since sibling `Id`s repeat), so a server that returns the
  same ribbon with those siblings reordered keeps one hash. Elements elsewhere
  keep their order, and a ribbon that does not parse as XML falls back to the
  text rule above. The artifact file and `sha256` stay as the server sent them.
  This is an ordinary v1 normalization rule, not a schema bump: a manifest
  written before it hashes its ribbons by the text rule, so one diff of an old
  extract against a new one reports its ribbons `changed`.
- No other artifact kind has XML canonicalization; it is deferred to a later
  schema version.
- `dump_manifest` sorts records by kind, then key, then content, artifacts by
  path and gaps by content, and writes sorted JSON keys. Two extractions of an
  unchanged org differ only in `extracted_at`.

## Versioning

`schema_version` is an integer that changes only on a breaking change; an
additive field keeps the version. A reader refuses an unknown `schema_version`
with an error naming the versions it reads (`1, 2 and 3`), before it reads anything
else, and ignores fields it does not know.

**Usage (#1095).** `record.usage` and `scope.usage_read` are optional and additive, so
`schema_version` stays as it was: a manifest written without `--with-usage` is byte-identical
to before, and a reader that ignores unknown fields reads one with usage.

**v1 to v2 (#1070).** v1 keyed the application ribbon `application` and the
default sitemap `default`, names an entity or a sitemap can hold, so two records
shared one key. v2 keys both `""`, the one part no entity logical name or
sitemap unique name can be. `schema_version` 2 is the only change, and it is a
break because a v2 record may carry an empty `key`, which the v1 schema forbids.
The reader accepts v1 and upgrades it on read: a `ribbon` record whose
`fields.entity` is null, or a `sitemap` record whose `fields.unique_name` is
null, takes key `""`, decided by the field and not by the old key, so a v1
manifest that holds the collision upgrades to distinct keys. The result reads as
v2, so `diff` pairs a v1 manifest with a v2 one. A v1 gap keyed `application` or
`default` is re-keyed to `""`, and gains a twin instead where the key is ambiguous (the
original stays, since it may name an entity's ribbon or a named sitemap). A v1 record that is neither of
those keeps its key. A v1 `solution_membership` or `dependency` row that references
the application ribbon or default sitemap (its `member_key`, `dependent_key` or
`required_key` `application` or `default`) is re-keyed to `""` too, unless the manifest
also holds an entity `application` ribbon or a sitemap named `default`: the row cannot
say which of the two it meant, so it keeps the v1 reference and does not pair with its
v2 twin in a diff. A v1 null ribbon or sitemap keyed by any other name is
refused. One consequence: artifact file names are derived from the key, so a
v2 extraction names these two files `component-<hash>` where a v1 one named
them `application-<hash>` and `default-<hash>`; `diff` pairs a record's
artifacts by their name within the record (`ribbon.xml`), not by path, so it is
unaffected.

**v2 to v3 (#1100).** v3 adds the `ribbon_diff` kind, with a coverage entry for it, and
the matching `gaps[].kind`. A v2 record never changes, but a v2 manifest has no
`ribbon_diff` coverage and the v3 schema requires one, so the version moves. The reader
accepts v1 and v2 and upgrades them on read: it adds `ribbon_diff` coverage `unsupported`
with the error `not read: a schema_version N extraction predates the ribbon_diff kind`, so a
diff or an assessment reads the kind as not extracted and never as an empty, complete one.

## Ribbon diffs

A `ribbon_diff` record is read from a solution export (`ExportSolution`), not from the
ribbon reads: the export's `customizations.xml` holds the application's `RibbonDiffXml` and
one per entity, which is the customization (the `ribbon` record is the merged ribbon). A
run exports the **Default** solution for the whole org, or each `--solution` it names, in
order; the first solution to hold an entity's diff wins. The zip is streamed to disk,
read once and deleted, and the export call waits up to 30 minutes (measured on a CRM 2011
org: 258 s and 88 MB for the Default solution).

Dataverse online refuses to export the Default solution, so a whole-org run on a cloud
profile rebuilds one diff per entity from the unmanaged rows of the `ribbondiffs` and
`ribboncommands` tables (`CustomActions` and `CommandDefinitions` only, no managed diff):
`ribbon_diff` coverage is `partial` with a gap saying what is not read, and `unsupported`
(with the reason) only if a table read faults. A `--solution` run reads the named solutions'. A managed solution cannot be exported
either, so each one named is a `ribbon_diff` gap with a one-line reason (the server's
fault is never recorded), and the kind is `unsupported` when every named solution is
managed. A failed export is `failed`, or `partial`
where some solutions read, never `complete`; a re-run resumes it from its checkpoint.

## What the existing read paths give an extractor

For each kind, the v9/cloud read path an extractor can reuse today and what it
lacks. Measured from the code in #1048. The CRM 2011 path reads through
`crm/core/legacy_reads.py` (`crm legacy metadata` and `crm legacy components`),
which pages every FetchXML read to its last page.

| Kind | Reusable read path (v9/cloud) | Lacks |
|---|---|---|
| entity | `metadata.list_entities` (`crm metadata entities`) | the GUID (`MetadataId` is not selected); `entity_info` has it per entity |
| attribute | `metadata.list_attributes` (`crm metadata attributes`) | nothing for v1 (it selects `MetadataId`) |
| relationship | `relationships.list_relationships` (`crm metadata relationships`) | the GUID |
| global option set | `optionsets.list_optionsets` (`crm metadata list-optionsets`) | the GUID; `get_optionset` has it per set |
| local option set | `metadata.picklist_options` (`crm metadata picklist`), per attribute | a list reader |
| form | `forms.read_entity_forms` (`crm form list`), form XML via `crm form export` | reads main forms only unless `--all` or `--type` |
| form event handlers | `forms.list_form_handlers` (`crm form list-handlers`) | handler rows carry no id |
| form libraries | none | a list reader (libraries appear only on handler rows) |
| saved view | `views.read_entity_views` (`crm view list`) | public views only; fetch and layout XML are read but not returned |
| web resource | `webresource.list_webresources` (`crm webresource list`), content via `crm webresource get` | the GUID in the list |
| plug-in assembly | none | a list reader (only register and unregister exist) |
| plug-in type | `plugin.list_types` (`crm plugin list-types`) | nothing for v1 |
| SDK message processing step | `plugin.find_step` (one step by exact name) | a list reader |
| step image | `plugin.find_step_image` (one image by step and alias) | a list reader |
| process | `workflow.list_workflows` (`crm workflow list`), XAML via `crm workflow export` | definitions only (`type=1`), which is what v1 wants |
| custom workflow activity | `workflow.assess_workflow_migrations` (`crm workflow migration-assess`) scans XAML | a list reader; its read caps at 100 pages and says nothing when it stops |
| solution | `solution.list_solutions` (`crm solution list`) | nothing for v1 |
| solution membership | `solution.solution_components` (`crm solution components`) | the `solutioncomponentid` (it returns `objectid`) |
| dependency | `dependencies.retrieve_dependencies` (`crm metadata dependencies`) | reads one page only, and covers four kinds (entity, attribute, option set, relationship) |
| ribbon | `ribbon.retrieve_entity_ribbon` and `retrieve_application_ribbon` (`crm ribbon export`) | nothing for v1 (a ribbon has no GUID) |
| ribbon diff | none (`ExportSolution` and `customizations.xml`: `ribbon_diff.read_ribbon_diffs`; online whole-org: the `ribbondiffs` and `ribboncommands` tables, `ribbon_diff.rebuild_ribbon_diffs`) | nothing |
| sitemap | none (`sitemap._load` reads one by id, for edits) | a list reader |

Two more limits apply across kinds:

- `D365Backend.get_collection(max_pages=N)` stops at the cap and returns the
  rows it has with no truncation signal, so a capped read looks complete. An
  extractor must detect the cap itself to record `truncated`. Metadata list
  endpoints (`EntityDefinitions` and the rest) return their full set in one
  response, so they do not page.
- `solution export-spec` is not a source for a migration inventory: it is apply-seedable
  only ([ADR 0019](../adr/0019-export-spec-apply-seedable-invariant.md)). It
  skips plug-ins and processes, and reads main forms only, so other forms drop
  out without a skipped entry. `org brief` is a capped summary (name lists
  capped at 200, the workflow total read from one page), not a migration
  inventory.
