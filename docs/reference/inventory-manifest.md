# Inventory manifest v1

A **migration inventory** is one extraction's descriptive record of an org's
customizations ([ADR 0032](../adr/0032-inventory-descriptive-contract-keyed-by-component-key.md)).
This page defines its format, `schema_version` 1, which the CRM 2011 path and
the v9 on-prem and cloud paths all emit. No command writes one yet: the
extractors land in later slices of #1034.

The format ships in the `crm` package as a JSON Schema (draft 2020-12),
`crm/schemas/inventory-manifest-v1.schema.json`, and as typed records with a
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
(`artifact_path(..., discriminator=<id>)`), so each keeps its own file. The
reader refuses an artifact path that two records share.

## Header

| Field | Value |
|---|---|
| `schema_version` | `1` |
| `source.platform` | `crm2011`, `onprem` or `cloud` |
| `source.org_url` | the org's `http` or `https` URL, never with credentials in it |
| `source.organization_id` | the organization id, or `null` |
| `source.version` | the org's build or version string |
| `extracted_at` | when the extraction ran (ISO 8601); the only time field |
| `cli_version` | the `crm` version that wrote the manifest |
| `scope` | `{"mode": "org", "solutions": []}`, or `{"mode": "solutions", "solutions": [<unique name>...]}` |
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

Two records with the same kind and key are **ambiguous**: both stay in the
manifest, and `Inventory.ambiguous()` reports them. Nothing picks one.

## Kinds and component keys

The key joins the kind's key fields, in the order below, with `|`. Each part
escapes `%` as `%25` and `|` as `%7C`, so different field values never give
one key. The code fields (`form_type`, `stage`, `mode`, `image_type`,
`category`, `dependency_type`) are the platform's integer codes, written in
decimal, which CRM 2011 and v9 share. Every other key field is a non-empty
string, and only the four fields the Notes column calls nullable may be
`null`, written as an empty part. A
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
| `solution_membership` | `solution`, `member_kind`, `member_key` | |
| `dependency` | `dependent_kind`, `dependent_key`, `required_kind`, `required_key`, `dependency_type` | |
| `ribbon` | `entity` | `null` is the application ribbon, key `application` |
| `sitemap` | `unique_name` | `null` is the default sitemap, key `default` |

### Fields beyond the key

`fields` may carry more normalized fields; a reader ignores the ones it does not
know. v1 defines three, so the source-map and packages slices can rely on them,
and the reader and the schema both check their shape when present:

| Kind | Field | Value |
|---|---|---|
| `form` | `libraries` | web resource names of the form libraries, in load order |
| `form` | `handlers` | `[{event, attribute, function, library, enabled}]`, `attribute` `null` for a form-level event |
| `process` | `activities` | plug-in type keys of the custom workflow activities the definition uses |

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
`description`. Typical gaps are an assembly registered on disk or in the GAC,
an ISV page, an external integration, and SQL report data sources.

## Normalization

- A text artifact's `normalized_sha256` hashes its bytes with a leading UTF-8
  BOM stripped and CRLF converted to LF. `sha256` always hashes the raw bytes.
- A binary artifact uses the raw hash for both.
- XML canonicalization is deferred to a later schema version.
- `dump_manifest` sorts records by kind, then key, then content, artifacts by
  path and gaps by content, and writes sorted JSON keys. Two extractions of an
  unchanged org differ only in `extracted_at`.

## Versioning

`schema_version` is an integer that changes only on a breaking change; an
additive field keeps the version. A reader refuses an unknown `schema_version`
with an error naming both versions, before it reads anything else, and ignores
fields it does not know.

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
