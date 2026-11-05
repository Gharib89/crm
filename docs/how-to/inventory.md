# How-to: inventory

`crm inventory extract` reads an org's customizations into a **migration
inventory**: a `manifest.json` in [inventory manifest v2](../reference/inventory-manifest.md)
format plus the raw artifacts it points at. An inventory describes what is
deployed; it is never a desired state for `crm apply`
([ADR 0032](../adr/0032-inventory-descriptive-contract-keyed-by-component-key.md)).
`crm inventory diff` compares two inventories offline (see
[Compare two inventories](#compare-two-inventories)), `crm inventory map-source`
matches one to an application repository (see [Map an inventory to source](#map-an-inventory-to-source)),
`crm inventory packages` groups one into work packages (see
[Group an inventory into work packages](#group-an-inventory-into-work-packages)), and
`crm inventory verify` judges recorded verification results against them (see
[Verify work packages](#verify-work-packages)), and `crm inventory assess` runs the
packaged compatibility rules over one (see
[Assess compatibility](#assess-compatibility)).

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
runs it too. It reads every v2 kind (`scope.mode: "org"` unless you pass
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

### Migration scope: stock components are left out

By default the inventory holds what someone built or changed. A **stock** component
(one the platform or an installed managed solution ships) is left out: a row that is
managed (`ismanaged`), or owned by a managed solution, on every kind that has the
column; for entities, attributes, relationships and option sets, one that is not
custom or is managed. The same rule runs on `crm2011` and v9, since both read
`ismanaged` and the owning solution. `--include-system` keeps stock and reproduces
the full read, for an audit or a baseline you will `diff` against another full
extract. The manifest header records the choice (`scope.system`: `excluded`, or
`included`, which is also what a manifest without the field means).

- A stock web resource, plug-in assembly, form, view, process or sitemap costs no
  payload read: the rows are read without their bodies, and the bodies are fetched
  only for the rows kept.
- A stock entity that a kept attribute, relationship, form, view or ribbon names stays
  as a reference row, and a kept step keeps its plug-in type and assembly, so keys
  resolve.
- The ribbon of a custom entity is kept. The ribbon of a system entity, and the
  application ribbon, is kept when it holds an entry outside the platform's own id
  families (`Mscrm.`, `AIBuilder.`, `msdyn`, and the other families in the code), or a
  `HideCustomAction`; one whose ids all fall in those families, or that holds none, is
  left out. A button hidden through a `CommandDefinition` display-rule override reuses
  stock ids, so that ribbon is left out; use `--include-system` to keep every ribbon.
- A solution membership or dependency naming a left-out component is not dropped
  silently: it is counted in a gap whose reason starts `platform:`. A kind whose rows
  carry no `ismanaged` is kept whole and noted as a gap.
- **Known limit:** a managed component customized in place keeps `ismanaged: true`, so
  the default scope misses it. Use `--include-system` to audit those.
- `--solution` names its own scope and keeps stock, so `--include-system` changes
  nothing there, and the manifest records `scope.system` as `included`.
- `crm inventory diff` refuses two manifests that differ in `scope.system`, since every
  stock component would read as added or removed; re-extract one side.

### Progress

A long extract prints progress on **stderr**, so stdout (the table, or the `--json`
envelope) is unchanged. Each stage prints a line when it starts, then about one line
a second, in a `key=value` shape a script can grep:

```text
[crm] extract stage=entity_detail total=340
[crm] extract stage=entity_detail done=120/340 reused=5 eta=~4m (estimate)
```

The stages run in order: `source` (3 reads), `records` (one paged read per record
kind: `done` counts finished reads, with the current `kind` and its `pages` so far),
`entity_detail` and `ribbons` (one unit per entity in scope), then `build`, which is
local. The `eta` appears in `entity_detail` and `ribbons` once five units have been
read this run, from their average read time; it is an estimate. The `records` stage
has no total page count, so it shows none. On a resumed run, units restored from the
checkpoint count as done (`reused`) and do not feed the estimate. Pass `--quiet`
(`-q`) to suppress the progress lines.

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
component only by id, but a web resource's content and a plug-in assembly's binary
are fetched only for the solutions' members (by id, in small batches), so the
largest payloads cost in proportion to the solution, not the org. Form, view,
process and sitemap XML are still read org-wide.

On disk, a finished artifact is a hard link to its staged payload in the checkpoint
(a copy where the filesystem refuses links), so a run's peak is about the finished
inventory, not twice it.

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
  of another scope", or of **another stock scope** (`--include-system` against a
  default checkpoint, or the reverse);
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
- **rows naming a stock component the default scope left out**: a membership or
  dependency of a kept component, counted per kind with a `platform:` reason, and
  **kinds with no `ismanaged`** (kept whole, since nothing says they are stock);
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
| `unchanged` | fields and normalized artifact hashes agree (a ribbon's hash ignores the order of its rule and command siblings) |
| `ambiguous` | the key repeats on either side, so it is never matched |
| `not_comparable` | the kind's coverage is not `complete` on both sides (`partial`, `failed`, `unsupported`, or no entry), or an extraction gap names the key and nothing else differs |

What a side could not read is never reported as removed, added or changed: a
record or artifact missing on a side whose extraction gap names the key is no
difference. A key a gap names is `not_comparable` unless a field or a hash differs
anyway (then `changed`, the gap in its `reason`), and both sides' gaps are listed
in the result. Different `--solution` scopes or source platforms are allowed; the header states both
sides and flags `same_platform` and `same_scope`. Two manifests that differ in
`scope.system` (one default, one `--include-system`) are refused.

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
`schema_version` this crm cannot read (the error names both versions and the
ones it reads, 1 and 2). A v1 manifest is upgraded on read, so a v1 extract and a v2
one of the same org pair their application ribbons and default sitemaps (key `""`
in v2, see the [manifest reference](../reference/inventory-manifest.md#versioning)).
The same two inventories give the same `--out` bytes, and swapping `A` and `B` swaps
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
| `matched` | a repo file's normalized hash (the inventory's BOM and CRLF normalization) equals the deployed `normalized_sha256` | an assembly: one project builds it; a type: a C# declaration of its full name sits in a project that builds its assembly (a `partial` class lists each file) |
| `stale` | one file lines up by name (its path ends with the web resource name, else it shares the file name) but the content differs: which side is newer is unknown | |
| `ambiguous` | several files hold the deployed content, or line up by name: all are listed | several projects build the assembly, or declare the type in it, or full (not `partial`) copies of the type sit in several files of one project |
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
  component maps to (an `.xml` or `.resx` file inside a C# project folder is a
  build input and is left out), and non-abstract classes deriving from `IPlugin` or
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
| `project_references` | `--out` only: each matched plug-in project that references other C# projects, with every project it reaches through `ProjectReference` (transitively); `crm inventory packages` reads it |
| `shared_projects`, `external`, `unmatched_source` | `--out` only: the lists, each entry with its paths (`external` entries carry `kind` `nuget` or `script`, `name`, `version` and `used_by`) |

Without `--json` it prints one row per kind with a column per status, then the
inventory and the totals. It exits **0** whatever the statuses, and **1** when the
inventory holds no readable manifest or an artifact it names is missing. The same
inventory and repository give the same `--out` bytes. A match is evidence, not
proof: equal hashes say the bytes agree, a declaration match only that the names
line up.

## Group an inventory into work packages

```bash
crm inventory map-source ./inv-v9 --repo ~/src/contoso-crm --out ./source-map.json
crm --json inventory packages ./inv-v9 --source-map ./source-map.json \
  --scenarios ./scenarios.json --out ./packages
```

`crm inventory packages INVENTORY --source-map FILE --out DIR` builds the inventory's
dependency graph and splits it into **work packages**: each one a connected group of
components and source files that must change together. It reads the inventory, the
source map `map-source --out` wrote for it, and the optional scenario file: it touches
no org and needs no profile. A source map made from another inventory (its
`manifest_sha256` differs) is refused; re-run `map-source` on this one.

The graph's edges come from:

- **The inventory**: platform dependencies; form libraries, event handlers and Url
  controls (read from each form's XML); ribbon and sitemap `$webresource:` references
  and URLs; a web resource's `src`, `href`, `openWebResource` and `/WebResources/`
  strings; a step to its plug-in type and assembly, an image to its step; a process
  to its activities.
- **The source map**: a component to its files and C# projects, and a plug-in
  project to each project it references, so two assemblies built on one shared C#
  project land together.

A reference read cannot resolve is an **unknown edge**, never dropped: it sits in
its package's findings, or, when its component is in no package (an entity, a system
web resource), in `unattached_unknowns` (an `out_of_scope` one in `unattached_out_of_scope`). Its `reason` is one of:

| Reason | Meaning |
|---|---|
| `dynamic` | the name is built at run time (`openWebResource(name)`, a `$webresource:` concatenation, a `{` placeholder in a URL) |
| `isv_page` | an `/ISV/` page: a custom ASP.NET page outside the inventory |
| `external_url` | a URL on a host other than the inventory's org |
| `unresolved_url` | a URL on the org's host, or a relative ribbon or sitemap URL, that names no web resource or ISV page (a custom page beside the CRM server, say); a URL under a folder the server ships (`/_root/`, `/tools/`, `/main.aspx` and the like, after an on-prem `/<org>` prefix) is skipped |
| `not_in_inventory` | a name or key the inventory holds no record for, in an inventory that read the whole org |
| `out_of_scope` | a web resource name the inventory holds no record for, in an inventory that did not read the whole org (`--solution`, or the default scope that leaves stock out): it may be a stock platform library or another solution's, so it is not proven missing. Listed, but counted apart from `unknowns`, and alone it forms no package |
| `unreadable` | an artifact that does not parse, or a malformed URL |

Only web resources, plug-in assemblies and types, steps and images, processes, forms,
ribbons and sitemaps join a group, with the source nodes; entities, attributes, option
sets and views are context. A system web resource or plug-in does not bridge two groups.
A group is a package when it holds a source-mapped component or an unknown. A package's
`id` is `pkg-` plus a hash of its sorted member ids, so the same members give the same
id on every run.

Each package file holds five fields. A field with nothing in it holds one
`{"status": "unknown", "reason": ...}` entry rather than an empty list:

| Field | Value |
|---|---|
| `source` | each source-mapped member with its map `status`, `files` and `projects`, plus each C# project the group references but no member builds (status `referenced`) |
| `consumers` | each form, ribbon, sitemap, process or step member, with the entities it sits on and the components that depend on it |
| `findings` | the unknowns: each unknown edge, each extraction gap on a member, and each member mapped `stale`, `ambiguous` or `missing_source` |
| `expected_behavior` | each scenario that seeds a member: its name, its `expected_behavior` lines and every package it spans, followed by an unknown entry when it names no lines |
| `verification` | what a check must cover: a form's libraries and handlers, a ribbon command's or sitemap entry's web resources, a step's message, entity, stage, mode, filtering attributes and images, a process's category and entity (its trigger is unknown in manifest v2) |

Each `verification` entry carries an `id`, `req-` plus a hash of the package id, the
requirement kind and the member, so a later run can match recorded results to it.
The file also lists `members`, the package's `edges` and the `scenarios` that tag it.

A scenario file names business processes and the components that start them:

```json
{"scenarios": [
  {"name": "Project approval",
   "seeds": ["form:new_project|2|Information", "process:1|new_project|Approve project"],
   "expected_behavior": ["Approving a project locks its budget fields"]}
]}
```

A seed is `<kind>:<key>`, the component key the manifest records. A seed missing from
the inventory is reported in `seeds_not_found`, and a scenario no package holds in
`unmatched_scenarios`. A seed the inventory holds but no package does (an entity, say)
is reported in `seeds_unpackaged`. A scenario is never dropped.

`data` holds the summary, and `--out DIR` (a new or empty folder) gets `index.json`
plus one `<package id>.json` per package:

| Key | Value |
|---|---|
| `schema_version` | the packages format version, `1` |
| `inventory` | the inventory it was built from, as in the source map |
| `packages`, `members`, `largest` | how many packages, their members in all, and the largest package's size; in `index.json`, `packages` is the list (`id`, `file`, `members`, `unknowns`, `out_of_scope`, `scenarios`) |
| `unknowns` | the findings across all packages, without the `out_of_scope` edges |
| `out_of_scope` | the `out_of_scope` unknown edges, in packages and in none; in `index.json`, those in no package are listed under `unattached_out_of_scope` |
| `scenarios`, `unmatched_scenarios`, `seeds_not_found`, `seeds_unpackaged` | the scenario count, the names no package holds, each `{scenario, seed}` not in the inventory, and each one in the inventory but in no package; in `index.json`, `scenarios` lists each one's packages and seeds |
| `unattached_gaps`, `unattached_unknowns` | extraction gaps on no package member, and unknown edges from a component in no package: counts in `data`, the lists in `index.json` (`unattached_out_of_scope` lists the `out_of_scope` ones apart) |
| `incomplete` | each kind not read `complete` in the inventory: its components may be missing from the graph |
| `packages_sha256` | in `data` only: the digest of the `--out` folder that a verification results file names (see [Verify work packages](#verify-work-packages)) |
| `out` | the `--out` folder |

Without `--json` it prints one row per package (members, unknowns, scenarios), then
the inventory, the unknown count and any unmatched scenarios, missing or unpackaged
seeds and unknowns in no package. It
exits **0** whatever the packages hold, and **1** when an input is unreadable or
`--out` is not empty. The same inputs give the same bytes in every file.

### Merge compatibility findings into the packages

```bash
crm --json inventory assess ./inv-v9 --target online --out ./findings.json
crm --json inventory packages ./inv-v9 --source-map ./source-map.json \
  --findings ./findings.json --out ./packages
```

`--findings FILE` (optional) reads the file `crm inventory assess --out` wrote. Each
blocker, deprecated, optional and unknown finding whose component is in a package joins
that package's `findings` list as a `compatibility` finding, keeping its `class`, `rule`
id, `evidence` and `sources` (the Microsoft Learn pages of its rule). A shared library's
finding appears once, in the package holding the library. `clear` findings are not
merged. Findings never create or regroup packages, so a finding whose component is in no
package is `unplaced`. Without the flag the output is byte-identical to before.

Every package in `index.json` gains `counts` (`blocker`, `deprecated`, `optional`,
`unknown`; `unknown` includes the graph's unknown edges and gaps), and the summary and
`index.json` gain `compatibility`: the `target`, the four totals and `unplaced` (a
count in the summary, a list of `{kind, key, class, rule}` in `index.json`). The table
adds the four count columns and a `findings in no package` line. A findings file made
from another inventory (its `manifest_sha256` differs), of an unsupported
`schema_version` or malformed exits **1**.

## Verify work packages

```bash
crm --json inventory verify ./packages --results ./run/results.json --out ./verify.json
```

`crm inventory verify PACKAGES --results FILE` is a completion gate: it checks each work
package's verification requirements against a **verification results file** someone
recorded, and gives each package a verdict. It runs no check and touches no org; it only
judges what was recorded, and never calls a package complete without a passing,
evidenced result for every requirement.

The results file is JSON, published as
[`verification-results-v1.schema.json`](https://github.com/Gharib89/crm/blob/main/crm/schemas/verification-results-v1.schema.json).
Like the manifest, a reader refuses an unknown `schema_version` and ignores an unknown
field.

```json
{"schema_version": 1,
 "packages_sha256": "<the packages output it was recorded against>",
 "results": [
  {"package": "pkg-3f2a9c1d0b7e", "requirement": "req-8c1f0a2b3d4e5f60", "outcome": "pass",
   "evidence": [{"path": "evidence/approve.log", "sha256": "<sha256 of the file>"}],
   "expected": "Approving a project locks its budget fields",
   "actual": "The budget fields were read-only after approval",
   "deployed": [{"component": "web_resource:new_/scripts/project.js", "sha256": "<content hash>"}],
   "errors": [], "environment": {"profile": "test-org", "org": "https://internalcrm.contoso.local/Org"},
   "open_decisions": [], "negative_control": false}
 ]}
```

| Field | Value |
|---|---|
| `packages_sha256` | the packages output the results were recorded against, as `crm inventory packages` returned it in `data`: a SHA-256 over the lines `<file sha256>  <file name>`, `index.json` first, then each package file in index order. A results file whose value differs is refused (exit 1) |
| `package`, `requirement` | the package `id` and the `verification` entry's `id` from the packages output |
| `outcome` | `pass`, `fail`, `blocked` (the check could not run: an environment down, say) or `not_tested` |
| `evidence` | the artifacts the check produced: each a path relative to the results file's folder, never absolute or with `..`, plus its SHA-256 |
| `expected`, `actual` | the assertion and what was observed (optional) |
| `deployed` | the deployed artifacts the check ran against: component id and content hash (optional) |
| `errors` | error messages or trace references (optional) |
| `environment` | the crm profile name and the org it ran in (optional) |
| `open_decisions` | manual decisions still open, reported with the package (optional) |
| `negative_control` | `true` for a deliberately introduced regression, whose check must fail (optional, default `false`) |

Each requirement takes the worst of its results (fail, blocked, not tested, pass). A
`pass` counts only with evidence, and only when every evidence file exists under the
results file's folder with its recorded hash. A package's verdict is the first that
applies:

| Verdict | When |
|---|---|
| `failed` | a requirement failed |
| `blocked` | a requirement is blocked |
| `not_tested` | a requirement has no result, records `not_tested`, or passed without evidence or with an evidence file missing or changed; or the package states no requirement |
| `complete` | every requirement passed with its evidence |

A **negative control** records a regression introduced on purpose, so it should `fail`
and is reported `held`. One that records `pass` means the check missed the regression:
it is reported in `escaped`, and its requirement counts as not tested, so the package is
never `complete`. A control recording `blocked` or `not_tested` is `not_run`.

A result naming a package or requirement id the packages output lacks is never
dropped: it is listed in `unknown_results` with the reason `unknown_package` or
`unknown_requirement`.

`data` holds:

| Key | Value |
|---|---|
| `schema_version` | the report format version, `1` |
| `packages_sha256` | the packages output judged |
| `counts` | packages per verdict: `failed`, `blocked`, `not_tested`, `complete` |
| `packages` | each package's `id` and `verdict`; in the `--out` file each also lists its `requirements` (`id`, `kind`, `member`, `status`, the `reason` a requirement is not tested: `no_result`, `recorded`, `no_evidence`, `evidence_unverified` or `control_escaped`, and its `controls`) and its `open_decisions` |
| `escaped` | each `{package, requirement}` whose negative control passed |
| `unknown_results` | each `{package, requirement, reason}` the packages output lacks |
| `out` | the `--out` file |

Without `--json` it prints one row per package (verdict, requirements passed), then the
verdict counts, any escaped controls and unknown results. A verdict is not a failure:
it exits **0** whatever the verdicts, and **1** when an input is unreadable or
malformed, the results file was recorded against another packages output, or `--out`
would overwrite an input (the results file, an evidence file or the packages output).
The same inputs give the same `--out` bytes.

## Assess compatibility

```bash
crm --json inventory assess ./inv --target online --out ./findings.json
```

`crm inventory assess INVENTORY --target onprem-9.1|online --out FILE` runs the
**compatibility rules** that ship with crm over an inventory and writes a findings file.
It is offline: it reads the inventory folder and touches no org
([ADR 0033](../adr/0033-second-shipped-skill-and-compatibility-rules-as-data.md)).

Each rule names the component kind, the source versions it applies to (compared with the
manifest's `source.version`), the targets, a class, a detection, the fix and at least one
Microsoft Learn page. The rules file is
[`crm/rules/compatibility-rules.json`](https://github.com/Gharib89/crm/blob/main/crm/rules/compatibility-rules.json),
published with its format,
[`compatibility-rules-v1.schema.json`](https://github.com/Gharib89/crm/blob/main/crm/schemas/compatibility-rules-v1.schema.json).
Version 1 covers web resource scripts (HTML and JavaScript) and plug-in assembly
registration:

| Rule | Class | Targets |
|---|---|---|
| `web-resource-xrm-page`: the `Xrm.Page` object | deprecated | both |
| `web-resource-xrm-utility`: `Xrm.Utility.openEntityForm`, `alertDialog` and the other deprecated methods | deprecated | both |
| `web-resource-get-server-url`: `getServerUrl()` | deprecated | both |
| `web-resource-crmform`: CRM 4.0 `crmForm` scripting | blocker | both |
| `web-resource-soap-2007`: the `/MSCrmServices/2007/` endpoint | blocker | both |
| `web-resource-soap-2011`: the 2011 `Organization.svc` or `Discovery.svc` SOAP endpoint | deprecated | both |
| `web-resource-odata-v2`: the `OrganizationData.svc` OData v2 endpoint | deprecated | both |
| `web-resource-jquery-static`: the jQuery 2.1.1 the app shipped, removed online | blocker | `online` |
| `assembly-storage-online`, `assembly-storage-onprem`: an assembly stored on disk or in the GAC | blocker online, optional on-prem | one each |
| `assembly-isolation-none`, `assembly-isolation-none-onprem`: an assembly registered without isolation | blocker online, optional on-prem | one each |

Every component gets at least one **compatibility finding**, keyed by component key,
never by GUID:

| Class | When |
|---|---|
| `blocker`, `deprecated`, `optional` | a rule matched; the evidence is the artifact `path`, its `sha256`, the first ten match `locations` (line and column) and the `match_count`, or for a registration rule the recorded `field` and `value` |
| `unknown` | no rule applies to the component (reason `no rule`: a kind no rule covers, or a binary web resource); a rule needs a field the inventory did not record (`field isolation_mode not recorded`, an inventory extracted before crm recorded it); a script artifact is missing or its hash differs from the manifest; an extraction gap names the component (`extraction gap: <reason>`); its kind was not read complete (`kind coverage partial`); or the inventory's source version is not a dotted version (extract writes `unknown` when it cannot read it), so no rule's version range can be decided |
| `clear` | the rules that apply found nothing |

A gap or incomplete coverage adds an `unknown` beside the rule findings, so a component
read only in part keeps both. The findings file
([`compatibility-findings-v1.schema.json`](https://github.com/Gharib89/crm/blob/main/crm/schemas/compatibility-findings-v1.schema.json))
has its own `schema_version`, names the inventory (`source`, `extracted_at`,
`cli_version` and the manifest's `manifest_sha256`), the target and the rules file's
`version` and `sha256`, and lists each rule a finding names under `rules_hit` with its
title, fix and sources. Gaps naming no component sit in `unattached_gaps`, and kinds not
read complete in `incomplete`. It is sorted, so two runs over the same inputs differ only
in `generated_at`.

`data` holds the same header, `counts` (`by_class`, and `by_kind`: each kind's findings
per class), the `unattached_gaps` count, `incomplete` and `out`. Without `--json` it
prints one row per kind with its findings per class. Findings are a verdict, so a blocker
still exits **0**; an unreadable inventory, an unknown `schema_version` or an `--out`
inside the inventory folder exits **1**.
