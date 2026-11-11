# How-to: inventory

`crm inventory extract` reads an org's customizations into a **migration
inventory**: a `manifest.json` in [inventory manifest v4](../reference/inventory-manifest.md)
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
[Assess compatibility](#assess-compatibility)), and `crm inventory convert` writes
patches for the code a rule can rewrite (see [Convert code](#convert-code)), and `crm inventory check-applied` proves a repository carries them (see
[Check that a repository carries the patches](#check-that-a-repository-carries-the-patches)); packages and assess can drop unused rows (see
[Filter and sort packages and findings by usage](#filter-and-sort-packages-and-findings-by-usage)).

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
  entity with none, such as an intersect entity, has no UI and no ribbon);
- the **ribbon diff** of the application and of each entity that has custom ribbon
  content: the customization itself, which the merged ribbon above does not separate
  from the stock one. It is read from a solution export (`ExportSolution`), so the
  org-wide read exports the Default solution (CRM 2011: about 4 minutes and 90 MB on a
  large org; the zip is streamed to disk, read once and deleted). Dataverse online
  refuses to export the Default solution, so there an org-wide run reads the unmanaged
  rows of the `ribbondiffs` and `ribboncommands` tables instead (Default-only
  customizations included) and rebuilds one diff per entity: the kind is `partial` with a
  gap, and `unsupported` only if those tables cannot be read;
  `--solution` exports the named solutions on any platform. The records are
  `ribbon_diff`, key the entity logical name (`""` for the application), artifact
  `ribbon-diff.xml`; an entity with an empty diff has none.

- the **reports**: one `report` record per report, keyed by its `name`, read by the same
  FetchXML read (`RetrieveMultiple` only on `crm2011`, so the SOAP allowlist gains
  nothing; the `reports` set on the Web API) in small pages, since each row can carry a
  large RDL body. A report's RDL body is one text artifact, `report.rdl`, and its
  `data_provider` is what the body's data sources say: `fetch` (every data source uses
  the FetchXML provider, what Dataverse online accepts), `sql` (a SQL data source,
  which online cannot run), or `unknown` (a mix of other providers, or a body that
  does not parse). Each record also holds `report_type`, `personal`, `custom` and
  `language`; the source GUID is recorded as the record `id` but is never matched on, so
  the same report on two orgs pairs by name. Two reports with one name are `ambiguous`
  in a diff ([ADR 0032](../adr/0032-inventory-descriptive-contract-keyed-by-component-key.md)),
  each keeping its own artifact path, and the run does not fail. A Reporting Services
  report (`reporttypecode` 1) whose body the endpoint did not return reads `unknown`
  with a one-line `data_provider_reason` naming the endpoint, never `fetch` or `sql`, and
  gets one [extraction gap](#extraction-gaps) naming it (see below). A link report
  (type 3) or other non-RDL report has no body and no gap.

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

- A stock web resource, plug-in assembly, form, view, process, sitemap or report costs
  no payload read: the rows are read without their bodies, and the bodies are fetched
  only for the rows kept.
- A stock entity that a kept attribute, relationship, form, view or ribbon names stays
  as a reference row (read `origin: system` when it is managed), so keys resolve.
- Stock flows down the plug-in chain: a plug-in type on a stock assembly, a step on a
  stock type and an image on a stock step are all left out, whatever their own
  `ismanaged` says (Dataverse marks platform steps unmanaged). Steps on the org's own
  assemblies are kept with their type and assembly, but a step the org registered on a
  managed (platform or vendor) assembly is left out too: audit those with
  `--include-system`.
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
- `crm inventory diff` refuses two whole-org manifests that differ in `scope.system`,
  since every stock component would read as added or removed; re-extract one side. A
  `--solution` manifest against a whole-org one is not refused: the diff is
  [restricted to the solutions](#compare-a-solution-target-with-a-whole-org-source).

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
`entity_detail` and `ribbons` (one unit per entity in scope), `ribbon_diffs` (one export
per solution in scope), then `build`, which is local. The `eta` appears in `entity_detail` and `ribbons` once five units have been
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
(`sitemap.xml`), ribbon XML (`ribbon.xml`) and ribbon diff XML (`ribbon-diff.xml`).

## Record usage with `--with-usage`

```bash
crm --json inventory extract --out ./inventory --with-usage
```

`--with-usage` adds a `usage` field (`state`, `signal`, `value`, `as_of`, `reason`) to
each `entity`, `plugin_assembly`, `plugin_type`, `step` and `process` record, from the
same probes as `--in-use` on the listings. A probe that fails or does not answer for a
row leaves it `unknown` with a reason, never `unused`. The probes run after the records
are read, so a default run (stock components left out) never probes stock entities.

A probe that faults is continue-and-report: its rows are `unknown`, a gap names it
(`usage of <group>`), the summary `errors` carries `usage/<group>: <error>`, and the
command exits **1** and keeps the checkpoint. A re-run on the same `DIR` skips the
groups that finished and retries the failed one. Progress prints `stage=usage-entity`,
`usage-plugins` and `usage-process`.

A single row's own read failure inside a probe that answered shows as `unknown` with its
reason on that row; only a probe that raises becomes a gap and an error.

`--usage-time-cap SECONDS` bounds the CRM 2011 job read (default 120) and needs
`--with-usage`. Without `--with-usage` the manifest and the run are unchanged.

`inventory diff` reports a `usage.state` change only when both manifests were read with
`--with-usage` (the header says `usage_compared`). A plain manifest against a usage one
still compares, and skips usage; it warns rather than refuses (a table `warning` line, and `meta.warning` under `--json`),
because nothing else differs.

## Narrow to solutions

```bash
crm --json inventory extract --out ./inventory --solution ContosoCore --solution ContosoSales
```

`--solution UNIQUE_NAME` (repeatable, on every platform, matched case-insensitively)
narrows the inventory to the named solutions: their solution records and memberships,
each member component, each attribute, local option set, form, view and ribbon of a
member entity, each ribbon diff in the named solutions' exports, each relationship with a member entity on either side, each
plug-in type of a member assembly (a type is usually not a solution component, so it
also follows its assembly), and each dependency whose dependent component is kept. On v9 and cloud, an entity a solution
holds without its subcomponents, or as a shell, brings only the subcomponents the
solution lists on their own. The manifest header records the scope with each name as
the org spells it (`scope.mode: "solutions"`, `scope.solutions`).

Entity details and entity ribbons are read only for member entities, and the
application ribbon is not read, so a narrowed run reads less per entity. The ribbon diffs
are read from each named solution's own export. A managed solution cannot be exported
(the platform answers `ExportManagedSolutionError`), so it is not: its ribbon diff is
an extraction gap with a one-line reason, and when every named solution is managed
(the usual shape of a migration target) the run still exits 0 with `ribbon_diff`
coverage `unsupported`. In a mix, the unmanaged solutions' diffs are read and the
managed ones are gaps. The org-wide component reads still run, because a solution membership names its
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

A table deleted after the entity list was read (a process that creates and drops
tables while the extract runs) is not a failed read. Its metadata or ribbon read
answers `Could not find an entity with name <x>` or `The entity with a name = '<x>'
... was not found in the MetadataCache`; the table is left out of the records (its
relationships too), and each kind it would have fed gets a gap, `<x>: no longer
exists, deleted since the listing`. The forms and views it owned are left out as
well, counted in one gap per kind. The outcome is checkpointed, so a resume does
not read the table again, and the kinds can still read `complete`. Only those two
messages naming the table count: a permission fault, a server fault or any other
404 stays an error. A solution membership naming the table is counted in the usual
unresolved-reference gap.

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
- the **shared data sources of SSRS reports** (`crm2011` and on-prem v9, not cloud): a
  report's shared data source is defined in SSRS, which the endpoint does not reach.
  The gap names no component, since it is about the data sources, not a report;
- a **Reporting Services report whose RDL body the endpoint did not return**: the
  `report` record is written with `data_provider` `unknown` and a reason, and one gap
  names it by `report` and its name with the same reason. The kind's coverage stays
  `complete` when every report record was read (coverage is `partial` or `failed` only
  when reading the records themselves faulted), and a diff reads only that report as
  `not_comparable`;
- the **ribbons of entities with no display label** (intersect and internal
  entities): such an entity has no UI, so its ribbon is not read (CRM 2011 faults
  the read), nor is the ribbon of an entity with no collection label in your
  language (the Web API faulted that read on a cloud org; on-prem v9 is not
  measured), and one gap lists them all;
- **parts of the ribbon diffs of a whole-org run on Dataverse online**: the platform
  refuses to export the Default solution, so the diffs are rebuilt from the unmanaged
  rows of the ribbon tables (`ribbon_diff` coverage is `partial`). Each holds its
  `CustomActions` and `CommandDefinitions`; its `RuleDefinitions`, `Templates` and
  `LocLabels` are not read (no rule content or label table was measured), nor is any
  managed diff. If a table read faults the kind is `unsupported` with the reason and
  the run still exits 0; `--solution` reads the named solutions' whole diffs;
- the **ribbon diff of a managed solution** in a `--solution` run: a managed solution
  cannot be exported, so each is a `ribbon_diff` gap, and the kind reads `unsupported`
  when every named solution is managed;
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

An `attribute` record's type facets (`max_length`, `format`, `default_value`,
`precision`, `min_value`, `max_value`, `option_set_global`, see the
[manifest reference](../reference/inventory-manifest.md#attribute-facets)) are
compared only where both manifests recorded them, so an older extract, which has
none, shows no facet change. A length that differs on both reads as one
`fields.max_length` change.

A `report` is paired by name, and its `data_provider` is compared like any field,
except where a gap names the report on either side: there a missing body makes the
provider `unknown` by construction, so a provider that differs is the gap, not a change,
and the report reads `not_comparable` unless something else differs. Against a manifest
older than v4, which reads `report` coverage `unsupported`, every report is
`not_comparable`.

A `form` record carries `active` (a v9 or cloud extract only), which `diff`
compares only where both manifests recorded it, so a CRM 2011 or older extract shows no
activation change. The CRM 2013 upgrade added new forms beside the 2011 ones and left
activation to the customer, so a form that is `active: false` in the later stage is not
shown to its users there. Against a 2011 source, read the later manifest's `form` records
for `fields.active: false`; between two v9 stages the same fact is a `fields.active`
change.

What a side could not read is never reported as removed, added or changed: a
record or artifact missing on a side whose extraction gap names the key is no
difference. A key a gap names is `not_comparable` unless a field or a hash differs
anyway (then `changed`, the gap in its `reason`), and both sides' gaps are listed
in the result. Different `--solution` scopes or source platforms are allowed; the header states both
sides and flags `same_platform` and `same_scope`. Two whole-org manifests that differ in
`scope.system` (one default, one `--include-system`) are refused.

### Compare when another team ran the upgrade hops (cloud-first)

When the database was moved through every stage by another team, you hold the CRM 2011
source (read-only) and the finished cloud org, and nothing between them. The `crm-migration`
skill calls this the cloud-first route. In order:

1. Confirm both orgs: `crm legacy whoami` on the 2011 source, `crm connection whoami` on the cloud org.
2. Extract the 2011 source with `--with-usage`.
3. Extract the cloud org with `--with-usage` and the same scope. Two scopes compare different things.
4. `crm inventory diff` the 2011 inventory against the cloud one.
5. Ask the team that ran the hops for a handover (below).
6. Map the cloud inventory to the application repository with `crm inventory map-source`, then run
   `assess`, `packages` and `convert` against it: it records the deployed
   code, and the OData v2 rewrite needs the entity set names only a v9 extract records.
7. Patch, build and verify on the cloud dev or test org.

With no stage observed between the two, a `removed` or `changed` entry cannot be tied to a hop.
Each one becomes a question for the team that ran the hops, or an unknown in the report; none is
read as a defect or as expected on its own. The staged path's rule of stopping before the next stage
does not apply: there is no next stage.

Ask that team for:

| Request | What it lets you conclude |
|---|---|
| One read-only `crm inventory extract` at the 9.1 stage, with usage, in the same scope | Two diffs (2011 to 9.1, 9.1 to cloud) replace the single one, and each entry is tied to the hop it appeared in |
| The components they disabled, removed or changed to pass each hop and the on-premises to online validation | A `removed` or `changed` entry is a deliberate act, not a loss; an entry absent from the list is unexplained |
| The upgrade logs of each hop and of the validation | Components the upgrade tooling dropped or rewrote without anyone recording it |
| Whether forms were merged | If yes, a `removed` form may be a merge and not a lost one; if no, it is lost |
| Which SQL objects and SQL-based reports were dropped | A `removed` SQL-based report or component was dropped on purpose, and the cloud org no longer has that behavior |

A missing item is an unknown, never a guess.

### Compare a `--solution` target with a whole-org source

To check a migrated solution against its source, extract the target with
`--solution <name>` (a managed import is stock, so the default scope would drop it) and
leave the source at the default scope:

```bash
crm inventory extract --solution Pilot --out ./inv-target
crm --json inventory diff ./inv-source ./inv-target --out ./diff.json
```

With exactly one side a `--solution` run and the other a whole-org run, `diff` skips the
stock-scope refusal and compares only the narrowed side's keys plus the whole-org side's
rows of the **same-named solutions**: the solution, its memberships, the members they
name and the subcomponents (attributes, forms, views, ribbons, option sets, relationships)
of each **custom** entity member. A source member the target lacks reads `removed`, not
silently dropped. The other whole-org keys are not compared, only counted. The header's
`restricted` says so: `narrowed` (`a` or `b`), `solutions`, `missing` (named solutions
the whole-org side does not hold, compared by the narrowed side's keys alone) and
`left_out` (whole-org keys outside the comparison). Rows the narrowed side flags as stock
compare like custom ones, so a migrated component can read `changed` on `origin` or
`managed` alone. Such a key counts under `by_origin.custom` (the first origin across the
sides), so read its `fields` to tell an origin-only change from real drift. A **stock** entity the
solution holds is compared by the members listed, since the manifest cannot tell a full
hold from a shell: its other subcomponents are left out. Two `--solution` manifests diff
as before.

`data` holds the summary, and `--out FILE` writes the same summary plus every
key's entry:

| Key | Value |
|---|---|
| `header` | each side's `platform`, `version`, `org_url`, `extracted_at` and `scope`; `same_platform`, `same_scope`; `basis`, the evidence the verdicts rest on; `restricted` only for a `--solution` side against a whole-org side (see above) |
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
| `stale` | one file lines up by name (its path ends with the web resource name, else it shares the file name; a name with no file extension also matches the file name plus the extension its type implies, `.htm` or `.html` for HTML) but the content differs: which side is newer is unknown | |
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
  controls (read from each form's XML); ribbon, ribbon diff and sitemap `$webresource:`
  references and URLs; a web resource's `src`, `href`, `openWebResource` and `/WebResources/`
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
A group is a package when it holds a source-mapped component or an unknown, or, with
`--findings`, a process with a `blocker` or `deprecated` finding. A package's
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
merged. A `blocker` or `deprecated` finding on a process creates a package for the
process's group when no package holds it: an isolated dialog or workflow becomes a package
of one, carrying its `process_trigger` requirement, so `verify` judges it. `optional` and
`unknown` findings on a process create none, and findings never regroup packages. A finding
on any other kind whose component is in no package is `unplaced`. Without the flag the
output is byte-identical to before.

Every package in `index.json` gains `counts` (`blocker`, `deprecated`, `optional`,
`unknown`; `unknown` includes the graph's unknown edges and gaps), and the summary and
`index.json` gain `compatibility`: the `target`, the four totals and `unplaced` (a
count in the summary, a list of `{kind, key, class, rule}` in `index.json`). The table
adds the four count columns and a `findings in no package` line. A findings file made
from another inventory (its `manifest_sha256` differs), of an unsupported
`schema_version` or malformed exits **1**.

## Filter and sort packages and findings by usage

`crm inventory packages` and `crm inventory assess` can drop rows nobody uses and list the
used ones first. Both take `--in-use` (drop `unused` rows), `--exclude-unknown` (also drop
`unknown` rows; it needs `--in-use`, else exit 2) and `--sort usage` (used first, then unknown or not read, then unused;
ties keep the default order). They are offline and read no org.

A row's usage is the one `extract --with-usage` recorded
([Record usage](#record-usage-with-with-usage)), else one derived from the manifest alone:

| Row | Derived usage | Signal |
|---|---|---|
| Web resource a form, ribbon or sitemap points at | `used`, reason `static, referenced by <rows>` | `referenced_by` |
| Web resource nothing points at | `unknown`, reason `unreferenced`; never `unused`, since a script can be loaded by name at run time or from outside the org | `referenced_by` |
| Form, view or ribbon of an entity whose usage is `unused` | `unused`, reason `entity has no records (low confidence: ...)`, since the data may have been deleted or migrated | `entity_rollup` |
| Any other row | unchanged: its recorded usage, or none | |

An entity that is `unknown` or `used`, or has no usage, leaves its forms, views and ribbons
alone. Reports, option sets and chart or dashboard rows have no derivation and stay without
usage.

A row with no `usage` was not read, which is not `unused`: every flag keeps it, and a
manifest extracted without `--with-usage` loses no row to `--in-use`. On such a manifest
`--exclude-unknown` still drops the web resources nothing references, because their derived
usage is `unknown`.

- **`packages`**: each package gets `usage` (in its file and its `index.json` line), its
  members' usage rolled up: `used` if any member is, `unused` only if every member is, else
  `unknown`. A member with no usage counts as `unknown`; a package whose members have none
  at all has no `usage` field. The flags drop whole packages, the summary's
  `usage_filtered` counts them, and a scenario lists only the packages that remain. The
  table gains a `usage` column, `state (signal)`, or `not read` where there is none.
- **`assess`**: each finding gains `usage` when its row has one, so the file shows why a row
  is `unknown`. The flags drop the findings of the excluded rows, blockers included, `usage_filtered`
  counts them (in the summary, and in the findings file when non-zero), `counts.by_usage`
  (`used`, `unknown`, `unused`, `not_read`) counts what is left, and `--sort usage` orders
  the `findings` list.

```sh
crm --json inventory assess ./inv --target online --out ./findings.json --in-use
crm --json inventory packages ./inv --source-map ./map.json --out ./packages \
  --in-use --sort usage
```

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
crm --json inventory assess ./inv --target online --out ./findings.json \
  --source-map ./source-map.json --repo ./app-repo
```

`crm inventory assess INVENTORY --target onprem-9.1|online --out FILE [--source-map MAP --repo REPO]`
runs the **compatibility rules** that ship with crm over an inventory and writes a findings file.
It is offline: it reads the inventory folder (and, with `--source-map` and `--repo`, the plug-in
projects it names) and touches no org
([ADR 0033](../adr/0033-second-shipped-skill-and-compatibility-rules-as-data.md)).

Each rule names the component kind, the source versions it applies to (compared with the
manifest's `source.version`), the targets, a class, a detection, the fix and at least one
Microsoft Learn page, and optionally a `transform` id naming the rewrite `inventory convert`
applies (see [Convert code](#convert-code)). The rules file is
[`crm/rules/compatibility-rules.json`](https://github.com/Gharib89/crm/blob/main/crm/rules/compatibility-rules.json),
published with its format,
[`compatibility-rules-v1.schema.json`](https://github.com/Gharib89/crm/blob/main/crm/schemas/compatibility-rules-v1.schema.json).
The rules cover web resource scripts (HTML and JavaScript), plug-in assembly
registration, how forms and ribbon diffs call those scripts, classic dialogs (process records of category 1), what a classic workflow's stored XAML holds (process records of category 0) and the data provider of a custom report:

| Rule | Class | Targets |
|---|---|---|
| `web-resource-xrm-page`: the `Xrm.Page` object | deprecated | both |
| `web-resource-xrm-utility`: `Xrm.Utility.openEntityForm`, `alertDialog` and the other deprecated methods | deprecated | both |
| `web-resource-get-server-url`: `getServerUrl()` | deprecated | both |
| `web-resource-context-settings`: `getUserName()`, `getUserId()` and the other context methods that moved to `userSettings` and `organizationSettings` | deprecated | both |
| `web-resource-grid-row`: `GridRow.getData()` and `GridRowData.getEntity()` | deprecated | both |
| `web-resource-xrm-mobile-offline`: `Xrm.Mobile.offline` | deprecated | both |
| `web-resource-entity-save`: `formContext.data.entity.save()` | deprecated | both |
| `web-resource-keypress`: `addOnKeyPress` and the other key-press and auto-complete methods | deprecated | both |
| `web-resource-crmform`: CRM 4.0 `crmForm` scripting | blocker | both |
| `web-resource-soap-2007`: the `/MSCrmServices/2007/` endpoint | blocker | both |
| `web-resource-soap-2011`: the 2011 `Organization.svc` or `Discovery.svc` SOAP endpoint | deprecated | both |
| `web-resource-odata-v2`: the `OrganizationData.svc` OData v2 endpoint | deprecated | both |
| `web-resource-jquery-static`: the jQuery 2.1.1 the app shipped, removed online | blocker | `online` |
| `form-handler-no-execution-context`: an enabled form handler without `passExecutionContext` whose library carries a `web-resource-xrm-page` finding (kind `form`) | deprecated | both |
| `form-library-no-odata-adapter`: a form whose library list names a script web resource with a `web-resource-odata-v2` finding and has no OData v2 adapter listed ahead of it (an adapter is a library defining `global.CrmODataV2 =`; kind `form`) | deprecated | both |
| `web-resource-page-no-odata-adapter`: an HTML web resource (type 1) that loads a script web resource with a `web-resource-odata-v2` finding through `<script src>`, or holds the OData v2 URL in a string of an inline script, and loads no OData v2 adapter ahead of it (kind `web_resource`); a page whose scripts cannot be placed or read is `unknown` | deprecated | both |
| `ribbon-action-no-odata-adapter`: a ribbon command with a `JavaScriptFunction` on a script web resource with a `web-resource-odata-v2` finding and no action calling an OData v2 adapter ahead of it, or a `CustomRule` calling such a script (kind `ribbon_diff`); a library that cannot be read is `unknown` | deprecated | both |
| `ribbon-action-no-primary-control`: a ribbon `JavaScriptFunction` on a web resource library (`$webresource:` or a `/WebResources/` path) with a `web-resource-xrm-page` finding and no `PrimaryControl` among its `CrmParameter`s (kind `ribbon_diff`) | deprecated | both |
| `csproj-sdk-net462`: the plug-in project targets .NET Framework below 4.6.2 (no declared version counts) or references the SDK 5.0 `Microsoft.Xrm.Sdk` or `Microsoft.Crm.Sdk.Proxy` (kind `plugin_assembly`, with `--source-map` and `--repo`; see [Plug-in projects](#plug-in-projects-found-by-assess)) | blocker | both |
| `plugin-source-sql-client`: the plug-in project's C# creates or accesses `SqlConnection`, `SqlCommand` or `SqlDataAdapter` from `System.Data.SqlClient` or `Microsoft.Data.SqlClient` (kind `plugin_assembly`, with `--source-map` and `--repo`; see [Plug-in source](#plug-in-source-found-by-assess)) | blocker | `online` |
| `plugin-source-filesystem`: the plug-in project's C# creates or accesses `File`, `Directory`, `FileInfo`, `DirectoryInfo` or `FileStream` from `System.IO` (same conditions) | blocker | `online` |
| `plugin-source-registry`: the plug-in project's C# creates or accesses `Registry` or `RegistryKey` from `Microsoft.Win32` (same conditions) | blocker | `online` |
| `assembly-storage-online`, `assembly-storage-onprem`: an assembly stored on disk or in the GAC | blocker online, optional on-prem | one each |
| `assembly-isolation-none`, `assembly-isolation-none-onprem`: an assembly registered without isolation | blocker online, optional on-prem | one each |
| `process-dialog`: a classic dialog (a `process` record of category 1); Microsoft deprecates dialogs and documents business process flows or canvas apps as the replacement, with no automatic conversion | deprecated | both |
| `workflow-wait-condition`: a classic workflow (category 0) whose stored XAML holds a wait condition, the `Postpone` activity with or without a namespace prefix; a cloud flow has no equivalent (Microsoft's feature capability comparison) | optional | both |
| `workflow-custom-activity`: a classic workflow (category 0) whose stored XAML holds an `ActivityReference` whose `AssemblyQualifiedName` names an assembly other than `Microsoft.Crm.Workflow`; a cloud flow cannot run it | optional | both |
| `report-sql-datasource`: a custom Reporting Services report (`report_type` 1, custom) whose data provider is `sql`; Dataverse online runs Fetch-based reports and the on-premises-to-online migration removes custom SQL-based reports that were not converted, so rewrite its datasets in FetchXML (Microsoft documents no converter, and a rewritten report must still pass the Dataverse RDL sandbox) | blocker | online |

A report reads the data provider the extraction recorded, so it needs no RDL artifact. A
custom report on `fetch` is `clear`; one on `unknown` is an `unknown` finding carrying the
record's own `data_provider_reason` (a missing RDL body, or a shared data source defined only
in Reporting Services). Only custom Reporting Services reports are judged: a stock SQL report
and a custom link, Excel or Power BI report are `clear` for this rule, because a data provider
belongs to an RDL and Microsoft documents no outcome for stock reports. The `onprem-9.1`
target has no report rule, since SQL is the supported provider on premises.

The two workflow rules are `optional`, never `blocker`: Dataverse online still runs classic
workflows, and Microsoft says only to consider replacing them. They read the XAML artifact, so
a category 0 workflow with no stored XAML is `unknown` (`no text artifact`, `artifact
unreadable: <path>` or `artifact changed since extraction: <path>`), never `clear`. Business
process flows, actions, dialogs and modern flows are not judged by them. Real-time
(synchronous) mode is **not** judged: the inventory does not record a process's mode, so a
`clear` classic workflow says nothing about it. The rules share their patterns with
`crm workflow migration-assess`, which is live, judges real-time mode too and writes no
findings file.

Every component gets at least one **compatibility finding**, keyed by component key,
never by GUID:

| Class | When |
|---|---|
| `blocker`, `deprecated`, `optional` | a rule matched; the evidence is the artifact `path`, its `sha256`, the first ten match `locations` (line and column) and the `match_count`, for a plug-in registration rule or `process-dialog` the recorded `field` and `value`, and for the two form and ribbon rules the `path`, `sha256`, `match_count` and `registrations`: per flagged handler its `event`, `attribute`, `function`, `library` and current `passExecutionContext`, per flagged action its `command`, `function`, `library` and `crm_parameters`; for a plug-in project the repository `path`, the `sha256` of its normalized text, `target_framework_version`, `sdk_references` and `fired` (see below) |
| `unknown` | no rule applies to the component (reason `no rule`: a kind no rule covers, or a binary web resource); a rule needs a field the inventory did not record (`field isolation_mode not recorded`, an inventory extracted before crm recorded it); a script artifact is missing or its hash differs from the manifest; an extraction gap names the component (`extraction gap: <reason>`); its kind was not read complete (`kind coverage partial`); a form or ribbon rule cannot read its library (`library <name> is not in the inventory`, `is not a script web resource`, or the library's artifact problem) or its own XML (`artifact is not well-formed XML`); a mapped plug-in project cannot be read or judged (`project <path> unreadable: ...`, `project <path>: the project is not well-formed XML`); or the inventory's source version is not a dotted version (extract writes `unknown` when it cannot read it), so no rule's version range can be decided |
| `clear` | the rules that apply found nothing. For a process that means only that the packaged process rules found nothing (`process-dialog`, and for a classic workflow the two XAML rules): nothing about its mode or trigger, and a business process flow, an action and a modern flow have no rule, so they read `clear`; a report is `clear` on `online` when it is a custom Reporting Services report on the `fetch` provider, a stock report or a custom link, Excel or Power BI report (outside `report-sql-datasource`), and `unknown` `no rule` on `onprem-9.1`, which has no report rule |

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
still exits **0**; an unreadable inventory, an unknown `schema_version`, a source map made
from another inventory or an `--out` inside the inventory folder exits **1**. `--source-map`
without `--repo`, or `--repo` without `--source-map`, is a usage error (exit **2**).

### Plug-in projects found by assess

The inventory holds no project file, so `csproj-sdk-net462` reads the repository: with
`--source-map` and `--repo`, each plug-in assembly the map marks `matched` to exactly one
`.csproj` (the projects [convert](#plug-in-projects) retargets) gets a `blocker` finding when
its project

- declares a `TargetFrameworkVersion` below 4.6.2, or none, or
- references the SDK 5.0 `Microsoft.Xrm.Sdk` or `Microsoft.Crm.Sdk.Proxy` as a `Reference` item
  (a `PackageReference` is not one).

The floor is the one Microsoft documents: Dataverse supports a plug-in or custom workflow
activity assembly that targets .NET Framework 4.6.2 through 4.8, and the on-premises 9.1
documentation says the same, so the rule applies to both targets. Both pages are in the rule's
`sources`, under `rules_hit`. A project at 4.6.2 or later with package references yields no finding
of this rule, so the assembly keeps its other findings, or `clear`. The evidence's `fired` lists
the condition that held (`framework`, `sdk_references` or both), `locations` the declaration and
each reference, and `match_count` their number; a project that declares no version counts as one
framework match with no location. One function decides the floor and the SDK 5.0
references for both commands, so `assess` and `convert` agree on them. `convert` also refuses a
project that is not a class library (a `ProjectTypeGuids` entry or an `OutputType` other than
`Library`) as a whole-file manual review, which `assess` does not.

An assembly the map leaves unmapped is not read and gets no finding of this rule, so a run without
both flags is as it was. A project that is missing, not UTF-8 or not well-formed XML, or whose
`TargetFrameworkVersion` is declared under a condition or is not a version, is `unknown` with the
reason. An SDK-style project, which declares `TargetFramework` instead, is never flagged: convert
refuses it too.

### Plug-in source found by assess

The three `plugin-source-*` rules read the C# of the same projects, so they need `--source-map`
and `--repo` too, and judge `--target online` only: the on-premises 9.1 sandbox is not
the online one. An assembly the map leaves unmapped or ambiguous is not read. A `clear`
assembly, or any run without both flags, says nothing about its source.

A file is flagged when its syntax tree holds an object creation or a member access whose type
resolves to a listed type through the file's own `using` directives, `using` aliases, `using static`
or a fully qualified name. A `using` alone, a comment, a string literal and a type of the same name
declared in the same file are not uses, and `Path`, `Stream` and `MemoryStream` are not file
access. A target-typed `new(...)` takes its type from the declaration it initializes. A bare call, or a
bare capitalized name such as `LocalMachine`, under `using static` counts as a use of every listed
type imported that way unless the file declares the name: the syntax tree cannot say which type
owns the member, so a name like `Path.Combine` next to `using static System.IO.File` over-reports. The files read are the `.cs` files under the project's folder, less `bin`,
`obj` and any folder holding another project: a shared library reached through a
`ProjectReference`, or a file linked from outside the folder, is not scanned.

Each flagged file gives one `blocker` per rule. Its evidence is the file's repository `path`, the
`sha256` of its normalized bytes, the first ten `locations` (line and column of each use) and the
`match_count`. A file that cannot be read, is not UTF-8 or parses with errors is one `unknown` for the
file, naming no rule, with the reason, and the project's other files still report. These rules detect and
record only: they name no transform, so [convert](#convert-code) reads no `.cs` file and writes
no patch or ledger entry for them. The sandbox refactor stays work for a human or an agent.

## Convert code

```bash
crm --json inventory convert ./inv --findings ./findings.json --packages ./pkgs \
  --source-map ./source-map.json --repo ./app-repo --out ./patches
git apply ./patches/<package id>/<stem>.repo.diff
```

`crm inventory convert` rewrites legacy code by **transforms**, offline: it reads the
inventory, the `assess` findings and the `packages` output, and writes only under `--out`
([ADR 0034](../adr/0034-deterministic-code-conversion-writes-patches-only.md)). A rule
opts in with an optional `transform` id in the rules file; `web-resource-xrm-page` names
`global-context`. A component is converted only when its finding's rule names a transform.

Each transform reads JavaScript web resources (type 3), parsed with tree-sitter, so a string or
comment is never rewritten. A shadowed or locally bound `Xrm`, a qualified `window.Xrm` or
`parent.Xrm`, an optional chain and a call spanning lines become **manual-review entries** with
file, line, rule and reason, and are left unchanged. An HTML web resource (type 1) is read by
`odata-v2` alone, in its inline scripts; every other transform sends it to manual review.

| Rule | Transform | Rewritten | Manual review |
|---|---|---|---|
| `web-resource-xrm-page` | `global-context` | `Xrm.Page.context.<m>()` to `Xrm.Utility.getGlobalContext().<m>()` for `getClientUrl`, `getVersion`, `getQueryStringParameters`, `isOutlookClient`, `isOutlookOnline`, `prependOrgName` | an alias, any other use of `Xrm.Page.context`, an unknown method |
| `web-resource-context-settings` | `context-settings` | a direct call on `Xrm.Page.context` or `Xrm.Utility.getGlobalContext()`: `getUserName`, `getUserId`, `getUserLcid` to `userSettings.userName`, `.userId`, `.languageId`; `getTimeZoneOffsetMinutes()` to `userSettings.getTimeZoneOffsetMinutes()`; `getOrgLcid`, `getOrgUniqueName`, `getIsAutoSaveEnabled` to `organizationSettings.languageId`, `.uniqueName`, `.isAutoSaveEnabled` | a call with arguments; every `getUserRoles()` call (manual review) |
| `web-resource-get-server-url` | `server-url` | `<ctx>.getServerUrl()` to `<ctx>.getClientUrl()` (only the method name changes) when `<ctx>` is `Xrm.Page.context`, `Xrm.Utility.getGlobalContext()` or the standalone page form `GetGlobalContext()` | any other receiver (`foo.getServerUrl()`), a file that declares or binds its own `GetGlobalContext`, a call with arguments, a non-call read of `getServerUrl`. The equivalence of the two methods on a 2011 org is the rule's judgment (no Microsoft Learn page names `getServerUrl`): `inventory verify` proves the behaviour |
| `web-resource-grid-row` | `grid-row` | `<row>.getData().getEntity()` to `<row>.data.entity` | `getData()` or `getEntity()` alone (the file does not prove the receiver) |
| `web-resource-xrm-mobile-offline` | `mobile-offline` | `Xrm.Mobile.offline.<m>(...)` to `Xrm.WebApi.offline.<m>(...)` for `createRecord`, `retrieveRecord`, `retrieveMultipleRecords`, `updateRecord`, `deleteRecord` | `isOfflineEnabled` and any other use |
| `web-resource-entity-save` | `entity-save` | `<x>.data.entity.save()` to `<x>.data.save()` | `save(saveOption)` with an argument |
| `web-resource-xrm-utility` | `xrm-utility-review` | nothing | `openEntityForm`, `openQuickCreate`, `openWebResource`, `alertDialog`, `confirmDialog`, `isActivityType`: the signature or sync behaviour changes |
| `web-resource-keypress` | `keypress-review` | nothing | `addOnKeyPress`, `removeOnKeyPress`, `fireOnKeyPress`, `showAutoComplete`, `hideAutoComplete`: no API replacement |
| `web-resource-soap-2011` | `soap-2011-review` | nothing | every string naming `/XRMServices/2011/Organization.svc` or `Discovery.svc` (any letter case): a synchronous SOAP call cannot be proved equivalent to an asynchronous Web API call with another response shape, so each site is a hand rewrite to the Web API. A reference in a comment only, or in an HTML page, gets the whole-file entry |
| `web-resource-odata-v2` | `odata-v2` | with `--target-inventory`, in a script web resource and in the inline scripts of an HTML page: `req.open("GET", "<org>/XRMServices/2011/OrganizationData.svc/<Set>Set?$select=...", <async>)` on a variable bound only to `new XMLHttpRequest()` in the same function to `CrmODataV2.open(req, "GET", "/api/data/v9.0/<set>?$select=...", <async>)`, the entity set and attribute names read from the target inventory | a receiver bound to anything else (a parameter, another value, a reassignment), a `$filter` value built at run time outside a string or guid literal, a write, an `$expand`, a `$skip` (the Web API pages through `@odata.nextLink`), a `$select`-less query, a `$.ajax` or `fetch` call, a URL not built from string pieces, an entity or attribute the target inventory does not hold, an entity record with no `entity_set_name` (a CRM 2011 or older extract records none) |
| `form-library-no-odata-adapter` | `form-odata-adapter` | with `--adapter-resource`: a `<Library name="<adapter>" libraryUniqueId="{...}" />` inserted on the line of the form's first library that `odata-v2` rewrote in this run, so the adapter loads first | no `--adapter-resource`; an unmappable library list; no library of the form was rewritten in this run; the adapter already listed after that library; the rewritten libraries and the form sit in different work packages; `{package}` in the name with a form in several packages |
| `web-resource-page-no-odata-adapter` | `html-odata-adapter` | with `--adapter-resource`: `<script src="<adapter>"></script>` (the adapter's name relative to the page's folder) inserted on the same line immediately before the first script of the page that this run rewrote, a patched `<script src>` library or an inline script whose OData v2 calls `odata-v2` rewrote in place (the line count never changes) | no `--adapter-resource`; a page whose `<script>` tags cannot be placed (a tag in a comment or CDATA section, a script that writes another, `<script />`, an unclosed script, a page that is not UTF-8); no script of the page was rewritten in this run (a library whose call stayed in manual review gets no adapter edit); the adapter already loaded after that script; the rewritten libraries and the page sit in different work packages; `{package}` in the name with a page in several packages |
| `ribbon-action-no-odata-adapter` | `ribbon-odata-adapter` | with `--adapter-resource`: `<JavaScriptFunction Library="$webresource:<adapter>" FunctionName="CrmODataV2.load" />` inserted on the line of the first action of each command whose library `odata-v2` rewrote in this run, so the adapter loads first (the other actions keep their order and bytes) | no `--adapter-resource`; a command whose first patched action is not a direct child of its `<Actions>`; no library of the diff's commands was rewritten in this run; the adapter already called after that action; the rewritten libraries and the diff sit in different work packages; `{package}` in the name with a diff in several packages; a `CustomRule` calling a rewritten library (always manual review, see below) |
| `form-handler-no-execution-context` | `form-handler-context` | in a registered form handler: an `executionContext` first parameter (or the existing one), `var formContext = executionContext.getFormContext();` after the opening brace, each `Xrm.Page.<member>` in its own body to `formContext.<member>`; a helper the handler calls (any number of hops) gains a first `formContext` parameter and every call passes it; and `passExecutionContext="true"` on every enabled registration of it, on the form XML in the same work package | see below |
| `ribbon-action-no-primary-control` | `form-handler-context` | a ribbon `JavaScriptFunction` on a form command whose function now takes the form context: `<CrmParameter Value="PrimaryControl" />` added as the first parameter (the existing ones keep their order behind it), and the function gets a first `primaryControl` parameter with `var formContext = primaryControl;` after its opening brace, its `Xrm.Page.<member>` becoming `formContext.<member>`, its helpers threaded as for a handler | a command on a grid, subgrid, home page or application ribbon location (`PrimaryControl` is the grid context there), or one no control in the diff names whose id is not `Mscrm.Form.*`; a function that is also a form handler, also called by a `CustomRule`, or called by another command with different `CrmParameter`s, another diff outside the work package, a caller the entity's effective ribbon holds that no readable diff carries, or one already passing `PrimaryControl` |

`odata-v2` moves a synchronous or asynchronous OData v2 `GET` onto the Web API without touching the code that reads the
response. The call site keeps its `req.open` arguments and the sync or async flag (a call never becomes
synchronous when it was not), so only `req.open(` becomes `CrmODataV2.open(req, `. The adapter `crm-odata-v2-adapter.js`, written
once per work package next to the patched files, defines `CrmODataV2` and an instance-level `responseText` getter that
returns the v4 body in the v2 shape: `d.results`, `d.__count` (a string), `{Value}` for option sets and money (money as a
string), `/Date(ms)/` dates, lookups as `{Id, LogicalName, Name}` (an empty option set, money or lookup keeps its object with null members, as v2 sent it), and an error as `{message: {lang, value}}`. It also sets the
`OData-MaxVersion` and `OData-Version` headers and asks for the annotations the lookups need. A v2 reply carried `d.__next` for the next page; the adapter does not map `@odata.nextLink`, so a caller that follows pages sees the first one only. Entity set and attribute names
come from the target inventory (`--target-inventory DIR`, a v9 extract), never from pluralising a name, and a `$select` is
required because the v2 attribute spelling comes from it. Upload the adapter as a web resource and load it before the patched
scripts. A v9 extract records `entity_set_name` on each entity, so
the target needs no editing. A target from a CRM 2011 extract, or one written before the field existed, has none and sends
every site to manual review: re-extract it from the v9 org.

`form-odata-adapter` lists the adapter on a form. Pass `--adapter-resource <name>`, the name you will give the uploaded
adapter web resource (`{package}` expands to the work package id; the same name for several packages is an error, raised
before anything is written). The `<Library>` element goes immediately before the first library the form lists that
`odata-v2` rewrote, on the same line, with a `libraryUniqueId` derived from the form and the adapter name, so a rerun
writes the same patch. A form that also needs the handler fix gets one combined patch. A form that already lists the
adapter ahead of its rewritten libraries is left alone.

`ribbon-odata-adapter` does the same for a ribbon diff, one command at a time. A ribbon command runs its actions in order,
each naming its own library, so the transform inserts one `CrmODataV2.load` action (a no-op the generated adapter exports)
immediately before the first action of the command that calls a library `odata-v2` rewrote. Every command that has such an action
gets its own, because a fresh page load clears the adapter's global and any command can be the first one clicked. A
command that already calls the adapter ahead of that action is left alone, so convert on its own output changes nothing, and
one that also needs `PrimaryControl` gets one combined patch. A `CustomRule` in an `EnableRule` or `DisplayRule` runs while the
ribbon renders, before any action, so no inserted action reaches it: a rule calling a rewritten library is a manual-review
entry naming the rule and its commands, and the diff is not edited for it.

The load behaviour behind this was measured on Dataverse online 9.2 in both the Unified Interface and the classic web client:
an adapter action makes `CrmODataV2` defined for the later actions of the same command and for later commands within one page
load, a fresh page load clears it, and a `CustomRule` at render sees it undefined (probe on
[#1181](https://github.com/Gharib89/crm/issues/1181#issuecomment-6768663087)). The on-prem v9.x leg of that probe was not run, so
the on-prem behaviour is inferred.

`html-odata-adapter` does the same for an HTML page. It finds each `<script>` with the standard library's HTML parser and
checks every position against a plain scan of the page, so a page the two disagree on is not edited. A `src` resolves
against the page's own web resource name (a leading `/WebResources/` is dropped); an address on another host or above the
root, or a name matching only by file name in another folder, is not the library. An inline classic script is parsed with the
JavaScript grammar and its calls are rewritten inside the `<script>` body. The page gets one patch: the adapter tag goes
before whichever rewritten script comes first. The page's registration is all or nothing: when the adapter cannot be
registered (no flag, a different package, an unreadable tag layout), the page's inline rewrite is dropped too and both
entries are manual review, because a rewritten call without the adapter would throw. Assess matches an adapter
`<script src>` only to a web resource in the inventory, so the finding clears once the adapter is uploaded and
extracted. The ledger's `adapters` entry records
the name under `web_resource` when the flag is given. Upload the adapter under that name and publish the solution; convert never
touches the org.

`getUserRoles()` returned GUID strings and `userSettings.roles` holds `{id, name}` lookups, so
the call changes the element type for whatever reads the list, and is always a manual-review
entry. `data.save` is asynchronous and
takes a `saveOptions` object. A component whose findings name several transforms gets all their
edits in one patch, the transforms running in findings order, one ledger entry per rule.

`form-handler-context` rewrites a handler and its registrations together or not at all. The context
is the first argument the platform passes, so the form's positional `parameters` keep their order
behind it. The declaration sits on the line of the opening brace, so no line moves. Only handler
bodies and the helpers they reach change, and `Xrm.Page.context` stays
with `global-context`. The context is valid only during the event
([Learn](https://learn.microsoft.com/power-apps/developer/model-driven-apps/clientapi/clientapi-execution-context#use-context-objects-asynchronously)),
so a site in a nested function and any `async` handler are manual review. A function is also
manual review, with its form handlers left unflagged, when the library defines it twice or
refers to it elsewhere, reads `arguments` or already uses `formContext`; when it has an
`executionContext`-like first parameter and form `parameters`; when another registration already
passes the context or sits on a form outside the work package or one this run does not patch; or
when it is not a `function` declaration or a `name = function` assignment.

A **ribbon command** function takes the context the same way, from `PrimaryControl` instead of
`executionContext` ([Learn](https://learn.microsoft.com/power-apps/developer/model-driven-apps/pass-data-page-parameter-ribbon-actions#form-and-grid-context-in-ribbon-actions)),
and the ribbon diff's actions are patched in the same work package (the diff travels with its
entity's ribbon). `SelectedControl` is the grid context, so only a command shown on a form
(every control naming it sits under a `Mscrm.Form.` location) is converted, and only when no other
web resource names the function, no `CustomRule` calls it, and no action of the entity's
effective ribbon calls it from a command the readable diff does not carry (another solution's
diff, or an action the extraction missed: the diffs alone would leave that caller on the old
arguments). The emit gate checks the patched diff still maps to its actions and exactly the planned ones now pass `PrimaryControl`.

A **helper** the handlers and commands reach is threaded through the file's call graph: it gains a first
`formContext` parameter (so positional arguments keep their order), its own `Xrm.Page.<member>`
becomes `formContext.<member>`, and every call to it passes `formContext`. A handler with no
`Xrm.Page` of its own is rewritten when it calls a threaded helper. A helper is threaded only
when every use of its name is a plain, direct call from a rewritten handler or a threaded helper.
These are manual review, with the helper and its callers left whole: a name defined twice or used
other than as a call (a callback argument, `.call`, a property), a call from a nested function or
callback, top-level code, an optional chain or a function nothing threads, a recursive cycle, a
helper that is `async`, a named function expression, reads `arguments` or already uses
`formContext`, a name also used as a computed property (`ns["leaf"]`), a dotted helper whose
receiver is rebound inside a function or twice, and a helper whose name a form
registers (a handler this run leaves alone) or another web resource, ribbon (or ribbon diff) or
sitemap of the inventory mentions (the scan reads raw text with `\u` escapes decoded, so it can
only over-report: a caller there cannot be updated). `Xrm.Page` that no registered handler reaches
(a function nothing calls, or code outside any function) is manual review too, as is `Xrm.Page`
in an arrow function, callback or object method this pass does not track (not counted). The ledger's top-level `call_graph` counts,
per work package, the helpers `threaded`, the functions and top-level sites `unreachable` and
the helpers held back at a `boundary` (a name used outside the file).

An **emit gate** checks each rewritten file: the patched code must parse with no more
error nodes, and re-running the rule's detection must find exactly the rewritten sites
fewer, and the rewrite must keep the line count (so manual-review line numbers stay valid). A rejected plan writes nothing and becomes a manual-review entry. A form patch is gated the same way: the patched XML must still map to its handlers and exactly the planned handlers must now pass the context. The handler transform's JS rewrite is gated on its library rule's pattern (`web-resource-xrm-page`), which must lose exactly the rewritten `Xrm.Page` sites.

Under `--out`, per work package and component:

| File | Holds |
|---|---|
| `<package id>/<stem>.diff` | unified diff against the extracted text, normalized (no BOM, LF) |
| `<package id>/<stem>.patched.js` | the patched text |
| `<package id>/<stem>.repo.diff` | diff against the repository file, only when `--source-map` marks the web resource `matched` by hash |
| `ledger.json` | `schema_version` 1; one entry per package, component key and rule: `status` (`patched` or `manual-review`), `edits`, `original_sha256`, `patched_sha256`, `repo_path`, `files`, `manual_review` (each item has `file`, `line`, `rule`, `reason` and three optional, additive fields, written only when known: `snippet`, the statement holding the call site, trimmed and cut to 240 characters ending in `...`, absent on a whole-file item whose `line` is null; `replacement`, the rule's `fix` text, on every item from a rule in the rules file and none from the C# project retarget; `return_use`, set on every `getUserRoles()` item only: `assigned`, `returned`, `argument`, `member` for a property, method or index read off the result, `discarded` or `other`); a top-level `call_graph` maps each package holding a converted library to its `threaded`, `unreachable` and `boundary` counts; a top-level `adapters` maps each package that needs the OData v2 adapter to its `file`, `sha256` and `entity_sets` (and `web_resource` with `--adapter-resource`); a patched `odata-v2` entry also lists the adapter under `files.adapter` |

### Plug-in projects

With `--repo` (the checkout `map-source` read) and `--source-map`, convert also retargets each
plug-in assembly the map marks `matched` to exactly one `.csproj`; an assembly the map leaves
`ambiguous`, `candidate` or `missing_source`, and a project only referenced by others (a shared
library), is not touched. Without `--repo` no project is read, and `--repo` without
`--source-map` is a usage error (exit **2**). The project is rewritten as a whole to SDK-style:

| Old-style project | SDK-style result |
|---|---|
| `TargetFrameworkVersion` below 4.6.2 (or absent) | `<TargetFramework>net462</TargetFramework>`; a project already on 4.6.2 or later keeps it (`net472`, `net48`) |
| `Microsoft.Xrm.Sdk` and `Microsoft.Crm.Sdk.Proxy` references (the SDK 5.0 `HintPath` ones) | one `Microsoft.CrmSdk.CoreAssemblies` package reference, version `9.0.2.60` |
| `SignAssembly` and `AssemblyOriginatorKeyFile` | kept |
| explicit `Compile`, `None`, `EmbeddedResource` and `ProjectReference` items, other references, conditional property groups | kept as written; `EnableDefaultItems` and `GenerateAssemblyInfo` are `false`, so the project builds exactly its listed files and keeps its own `AssemblyInfo.cs` |
| `Configuration`, `Platform`, `ProjectGuid`, `ProductVersion`, `SchemaVersion`, `AppDesignerFolder`, `TargetFrameworkProfile`, `OutputType`, `FileAlignment` and the `Microsoft.Common.props` and `Microsoft.CSharp.targets` imports | dropped (the SDK supplies them) |

These are **manual-review entries**. A `Microsoft.Xrm.Client` or `Microsoft.Xrm.Sdk.Workflow`
reference (the package carries neither) and any ILMerge use are flagged on a patched project, the
reference or merge step kept as written. A project that is not a class library, is already
SDK-style, sets `ProjectTypeGuids`, has a `TargetFrameworkVersion` that is not a version, is not
well-formed, or is missing or not UTF-8 under `--repo`, is left untouched, with one whole-file
entry (no `line`).

The ledger entry has `kind` `plugin_assembly`, the assembly's component key, one entry per work
package holding it (`unpackaged` when none does); a patched entry has `edits` 1 and `repo_path`
the project's path.
Its `rule` and `transform` are both `csproj-sdk-net462`, the id of the rule
[`assess --source-map --repo`](#plug-in-projects-found-by-assess) flags the project with: the
repository, not the inventory, is what it detects on, and one detector decides both. Per project, `--out/<package id>/` holds
`<repo path, "/" as "__" and "_" as "_u">.diff` against the repository path and `.patched.csproj`, the full
project: copy it over the original, since the diff is against LF text and can fail on a CRLF file.
Builds stay outside convert's gate, on any host with the .NET SDK (Linux is enough).

A component with no transformable finding writes nothing. `--out` must be new or empty
and not inside the inventory. The repo diff derives from the deployed, normalized text, so
`git apply` can fail on a repository file with other line endings: convert it first, or
apply the `.patched.js` by hand. `data` holds `patched`, `manual_review`, `entries` and
`out`; an unreadable or foreign input or a bad `--out` exits **1**.

## Check that a repository carries the patches

```bash
git -C ./app-repo apply "$PWD/patches/<package id>/<stem>.repo.diff"
crm --json inventory check-applied ./patches --repo ./app-repo
```

`crm inventory check-applied CONVERT_OUT --repo PATH` is the gate between applying
convert's patches and building. It reads `ledger.json` and the checkout, writes nothing
(not the repository, not `CONVERT_OUT`), touches no org and needs no profile. For each
ledger entry that patches a repository file it hashes the file the way convert did
(no byte order mark, LF line endings, so a CRLF or BOM file compares correctly) and
reports one status:

| Status | The file's normalized hash |
|---|---|
| `applied` | equals the entry's patched hash |
| `not_applied` | equals its original hash: the patch was left out |
| `diverged` | equals neither: the file was edited since extraction |
| `missing` | the file is not in the repository |
| `not_checkable` | an entry with no repository file (a manual-review entry, or a patch for an inventory artifact only); listed with the reason |

Exit **0** when every checkable entry is `applied`; **1** otherwise, with the rows that are
not. A missing or empty `CONVERT_OUT`, or a ledger whose `schema_version` this crm does not
read, is a usage error (exit **2**). `data` holds `all_applied`, `counts` per status and
one row per entry (`package`, `kind`, `key`, `rule`, `status`, `path`, `reason`); with
`--json` the envelope's `ok` is `false` on exit 1. Convert's contract is unchanged:
it still writes only under `--out`, and applying a patch stays your `git apply`.
