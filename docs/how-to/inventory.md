# How-to: inventory

`crm inventory extract` reads an org's customizations into a **migration
inventory**: a `manifest.json` in [inventory manifest v1](../reference/inventory-manifest.md)
format plus the raw artifacts it points at. An inventory describes what is
deployed; it is never a desired state for `crm apply`
([ADR 0032](../adr/0032-inventory-descriptive-contract-keyed-by-component-key.md)).
`crm inventory diff` compares two inventories offline (see
[Compare two inventories](#compare-two-inventories)), and `crm inventory map-source`
matches one to an application repository (see [Map an inventory to source](#map-an-inventory-to-source)).

Extraction runs on **every profile**. A `crm2011` profile is read over the CRM 2011
SOAP endpoint (see [How-to: legacy](legacy.md) to create one); a v9 on-prem or
Dataverse online profile is read over the Web API. The manifest's `source.platform` says
which: `crm2011`, `cloud` when the profile authenticates with `oauth`, else
`onprem`. See the [CLI reference](../reference/cli.md) for every flag.

## Extract

```bash
crm --json inventory extract --out ./inventory --profile legacy2011
crm --json inventory extract --out ./inventory --profile prod
```

The run is **read-only**, and nothing is published first: the inventory holds the
**published** customizations (`publication: "published"`). On a `crm2011` profile
every request goes through the same SOAP allowlist as `crm legacy`, so
`PublishXml`, `PublishAllXml` and `ImportSolution` are refused. On a v9 profile
every call is a GET, so a [read-only profile](../adr/0021-read-only-profile-tty-gated-guardrail.md)
runs it too. It reads every v1 kind (`scope.mode: "org"` unless you pass
`--solution`):

- entities, global option sets, and each entity's attributes, relationships and
  local option sets, from the metadata reads (`EntityDefinitions`,
  `GlobalOptionSetDefinitions` and one `RetrieveEntity` per entity on the Web API);
- solutions, web resources, plug-in assemblies, plug-in types, steps, step images,
  processes (workflow definitions, type 1), forms, views, sitemaps, solution
  membership and dependencies, each with one FetchXML read paged to its last page
  by the paging cookie (SOAP `RetrieveMultiple` on `crm2011`, a `?fetchXml=` query
  on the Web API). Dependencies are read whole, not one component at a time: the
  `dependency` entity on `crm2011`, the `dependencies` entity set on the Web API;
- the application ribbon and the ribbon of each entity with a display label (an
  entity with none, such as an intersect entity, has no UI and no ribbon).

Each entity gets one metadata read and one ribbon read, so the whole-org read takes
tens of minutes on a large org.

`DIR` receives:

```text
<DIR>/
  manifest.json
  artifacts/<kind>/<slug>-<hash>.<name>
```

The artifacts are web resource content (`content.js`, `content.png`, ...), the
plug-in assembly binary (`content.dll`), workflow XAML (`workflow.xaml`), form XML
(`form.xml`), view fetch and layout XML (`fetch.xml`, `layout.xml`), sitemap XML
(`sitemap.xml`) and ribbon XML (`ribbon.xml`).

## Narrow to solutions

```bash
crm --json inventory extract --out ./inventory --solution ContosoCore --solution ContosoSales
```

`--solution UNIQUE_NAME` (repeatable, on every platform, matched case-insensitively)
narrows the inventory to the named solutions: their solution records and memberships,
each member component, each attribute, local option set, form, view and ribbon of a
member entity, each relationship with a member entity on either side, and each
dependency whose dependent component is kept. On v9 and cloud, an entity a solution
holds without its subcomponents, or as a shell, brings only the subcomponents the
solution lists on their own. The manifest header records the scope with each name as
the org spells it (`scope.mode: "solutions"`, `scope.solutions`).

Entity details and entity ribbons are read only for member entities, and the
application ribbon is not read, so a narrowed run reads less per entity. The
org-wide component reads still run, because a solution membership names its
component only by id, so expect the component reads to take as long as a whole-org
run.

An unknown solution name is a read error on the `solution` kind: the run goes on,
exits **1** and keeps its checkpoint (see [When a read fails](#when-a-read-fails)).
If the solution or solution membership read fails or is cut short, the scope may be
missing components, so every kind but `solution` is marked `partial` or `failed` too.

## The summary

`data` is a short summary; the manifest holds the records, coverage and gaps:

| Key | Value |
|---|---|
| `out`, `manifest` | the output folder and the `manifest.json` path |
| `records` | the number of records written |
| `kinds` | per kind, `{status, records}`; `status` is `complete`, `partial` or `failed` |
| `gaps` | the number of extraction gaps |
| `errors` | one line per failed read |
| `reused` | per kind, the reads taken from the checkpoint instead of the org |
| `resumed` | `true` when the run continued an earlier one |
| `complete` | `true` when every kind read without an error |

## Resume an interrupted run

Each finished read (one metadata request, one entity's detail, one
FetchXML page, one ribbon) is saved under `DIR/.checkpoint/` before the
next starts. To resume, **re-run the same command on the same `DIR`**: finished
reads are reused, and a kind's paging continues after its last finished page from
that page's paging cookie. `extracted_at` stays the first run's start time.

`DIR` is refused when:

- it holds a checkpoint of **another org** (a different profile URL);
- it holds a checkpoint of **another scope** (a different `--solution` set, or a
  narrowed run against a whole-org checkpoint, or the reverse): "holds a checkpoint
  of another scope";
- it holds files but **no checkpoint**, which includes a finished inventory, so a
  new extraction is never mixed into an old one. Pass an empty or new folder.

## When a read fails

A read that faults is recorded and the run goes on (continue-and-report,
[ADR 0007](../adr/0007-record-clone-no-rollback-continue-and-report.md)): its kind's
coverage is `failed` (no records) or `partial` (some records), its error lands in
the coverage entry and in `errors`, and the manifest is still written. The command
then exits **1** with `ok: false` and the summary still in `data`, and the
checkpoint is kept, so a re-run on the same `DIR` retries only the reads that
failed. A page that reports more records but carries no paging cookie marks its
kind `truncated` and `partial`.

An auth failure (401) or an unreachable org is not per read: it stops the run with
the standard error envelope and no summary. The checkpoint keeps every finished
read, so re-run once the org answers again.

The checkpoint is removed once a run reads everything without an error.

## Extraction gaps

The manifest's `gaps` names what the endpoint (the CRM 2011 SOAP endpoint or the
Web API) cannot read, or failed to read, rather than leaving it out; each reason
names the endpoint:

- a **plug-in assembly stored on disk or in the GAC**: its record is written, but
  the binary lives on the CRM server, not in the database;
- an **ISV page** (`/ISV/...`) referenced from a web resource or the sitemap: a
  file on the CRM web server;
- **external integrations** (every platform): services that call the org or that
  its plug-ins and processes call;
- the **SQL data sources of SSRS reports** (`crm2011` and on-prem v9, not cloud);
- the **ribbons of entities with no display label** (intersect and internal
  entities): such an entity has no UI, so its ribbon is not read (CRM 2011 faults
  the read), nor is the ribbon of an entity with no collection label in your
  language (the Web API faulted that read on a cloud org; on-prem v9 is not
  measured), and one gap lists them all;
- **rows naming a component this inventory does not record** (a solution
  membership or dependency on a kind v1 does not define, whose own read failed,
  whose own row has no component key, or, in a `--solution` run, that may belong to
  an entity outside the named solutions; and a plug-in type, step or step image
  that cannot be tied to its assembly, type or step, or a step with no message name,
  which a `--solution` run leaves out),
  counted per kind and component type;
- **rows the key contract cannot hold** (a component key field the org left empty,
  such as a plug-in step with no name), counted per kind with the first error;
- the **source identity and build**, when its `WhoAmI` or `RetrieveVersion` read
  failed: the manifest's `version` then reads `unknown`, and the gap carries the
  fault. A re-run retries the read.

Collect the others by hand from the server or the people who run the integrations.

## Compare two inventories

```bash
crm --json inventory diff ./inv-2011 ./inv-v9 --out ./diff-2011-v9.json
```

`crm inventory diff A B` compares inventory `A` (the earlier stage) with `B`, from
any stages: CRM 2011, on-prem v9 or cloud. It reads the two folders only and
touches no org, so it needs no profile. Records are matched by **component key**
within each kind, never by GUID, and each key gets one verdict:

| Verdict | Meaning |
|---|---|
| `added` | the key is only in `B` |
| `removed` | the key is only in `A` |
| `changed` | a recorded field differs, or an artifact's `normalized_sha256` does |
| `identity_only` | only the GUID differs (both sides record one); kept apart from `changed`, since a migration route may re-create components |
| `unchanged` | fields and normalized artifact hashes agree |
| `ambiguous` | the key repeats on either side, so it is never matched |
| `not_comparable` | the kind's coverage is not `complete` on both sides (`partial`, `failed`, `unsupported`, or no entry), or an extraction gap names the key and nothing else differs |

What a side could not read is never reported as removed, added or changed: a
record or artifact missing on a side whose extraction gap names the key is no
difference. A key a gap names is `not_comparable` unless a field or a hash differs
anyway (then `changed`, the gap in its `reason`), and both sides' gaps are listed
in the result. Different scopes or source platforms are allowed; the header states both
sides and flags `same_platform` and `same_scope`.

`data` holds the summary, and `--out FILE` writes the same summary plus every
key's entry:

| Key | Value |
|---|---|
| `header` | each side's `platform`, `version`, `org_url`, `extracted_at` and `scope`; `same_platform`, `same_scope`; `basis`, the evidence the verdicts rest on |
| `counts` | per kind, the number of keys per verdict (a verdict with no key is left out) |
| `by_origin` | per origin (`custom`, `system`, `unknown`), the number of keys per verdict, so platform-added system components don't drown custom drift; a key whose origin differs between the sides counts under the first of `custom`, `unknown`, `system` |
| `incomplete` | each kind not read `complete` on both sides, `{a, b}` its status there (`absent` with no coverage entry), listed even when neither side holds a record |
| `gaps` | `{a, b}`: each side's extraction gaps |
| `out` | the `--out` path, or `null` |
| `entries` | `--out` only: one per key, sorted by kind then key, with `kind`, `key`, `verdict`, `origin`, and where they apply `fields` (`{name, a, b}`; a `fields` entry is named `fields.<name>`), `artifacts` (`{path, a, b}`, the normalized hashes, `null` on the side that lacks it), `ids` (`{a, b}`) and `reason` |

Without `--json` it prints one row per kind with a column per verdict, then both
sources and the incomplete kinds.

A difference is a result, not a failure: the command exits **0**. It exits **1**
when either folder holds no readable manifest, or one of them has a
`schema_version` this crm cannot read (the error names both versions). The same
two inventories give the same `--out` bytes, and swapping `A` and `B` swaps
`added` and `removed`.

Agreement is evidence, not proof: equal keys, fields and hashes say the recorded
customization matches, never that the two orgs behave alike.

## Map an inventory to source

```bash
crm --json inventory map-source ./inv-v9 --repo ~/src/contoso-crm --out ./source-map.json
```

`crm inventory map-source INVENTORY --repo PATH` matches the inventory's custom web
resources, plug-in assemblies and plug-in types (custom workflow activities
included) to files and C# projects in a local checkout of the application
repository. It reads the two folders only: it touches no org, needs no profile, and
never builds or runs anything in the repository. System components are not mapped.
Each component gets one status:

| Status | Web resource | Plug-in assembly or type |
|---|---|---|
| `matched` | a repo file's normalized hash (the inventory's BOM and CRLF normalization) equals the deployed `normalized_sha256` | an assembly: one project builds it; a type: a C# declaration of its full name sits in a project that builds its assembly |
| `stale` | one file lines up by name (its path ends with the web resource name, else it shares the file name) but the content differs: which side is newer is unknown | |
| `ambiguous` | several files hold the deployed content, or line up by name: all are listed | several projects build the assembly, or declare the type in it |
| `candidate` | minified content on one side only keeps the hashes apart (a line of 500 characters or more, or `.min.` in the name), or the file looks generated (a `.ts` source for a `.js`, an un-minified name for a `.min.js`); `reason` says which | the type is declared in a project that builds another assembly, or the assembly's project exists but the type is not found |
| `missing_source` | no candidate at all | no candidate at all |

A project's assembly is its `AssemblyName`, or the project file name when it has
none. Type declarations are found by a lexical scan of `.cs` files, block-scoped and
file-scoped namespaces alike, nested types named `Outer+Inner`; a `.cs` file belongs
to the project in its nearest folder holding a `.csproj`. `$(MSBuildProjectName)` in
an `AssemblyName` is expanded; any other MSBuild property is compared as written.
Folders named `bin`, `obj` or `node_modules`, dot folders, and a `packages` folder
beside a `.sln` (NuGet's restore folder) are not scanned.

The map also lists:

- **Shared web resources**: one more than one component references, through a form
  library or handler, a ribbon or sitemap `$webresource:` reference, or another web
  resource's `src` or `href`. Each web resource entry lists its `consumers`.
- **Shared projects**: a C# project two or more matched plug-in projects reference
  (`ProjectReference`), a shared library.
- **External references**: NuGet packages (`PackageReference`, `packages.config`)
  and web resource scripts loaded from a host other than the inventory's org.
- **Unmatched source**: repo files a web resource could be deployed from that no
  component maps to, and non-abstract classes deriving from `IPlugin` or
  `CodeActivity` (directly or through the repo's own base classes) that no plug-in
  type names.

`data` holds the summary, and `--out FILE` writes the full map:

| Key | Value |
|---|---|
| `schema_version` | the source map's own format version, `1`; a reader refuses any other |
| `inventory` | the inventory it was built from: `manifest_sha256` (of its `manifest.json`), `platform`, `version`, `org_url`, `extracted_at` |
| `basis` | the evidence the statuses rest on |
| `incomplete` | each mapped kind not read `complete` in the inventory, with its coverage status: its components may be missing from the map |
| `counts` | per kind, the number of components per status (a status with none is left out) |
| `shared` | `{web_resources, projects}`: how many are shared |
| `external`, `unmatched_source` | how many there are |
| `out` | the `--out` path, or `null` |
| `components` | `--out` only: one per component, sorted by kind then key, with `kind`, `key`, `status`, `files`, `projects`, `reason`, `consumers` and `shared` |
| `shared_projects`, `external`, `unmatched_source` | `--out` only: the lists, each entry with its paths (`external` entries carry `kind` `nuget` or `script`, `name`, `version` and `used_by`) |

Without `--json` it prints one row per kind with a column per status, then the
inventory and the totals. It exits **0** whatever the statuses, and **1** when the
inventory holds no readable manifest or an artifact it names is missing. The same
inventory and repository give the same `--out` bytes. A match is evidence, not
proof: equal hashes say the bytes agree, a declaration match only that the names
line up.
