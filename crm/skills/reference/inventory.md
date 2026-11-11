# Migration inventory: extract, compare, map, package, verify, assess

`extract` reads an org, a `crm2011` profile included (`reference/setup.md`, Legacy
CRM 2011 orgs); the other five read files only and need no profile.

## Extracting an inventory (`crm inventory extract`)

A migration inventory is a read-only, descriptive record of an org's customizations: a
`manifest.json` (schema v4, which also reads v1 to v3: records keyed by component key,
per-kind coverage, extraction gaps) plus raw artifacts (XML, XAML, web resource content, assembly
binaries). It describes what is deployed; it is never a spec for `crm apply`.

- **It runs for tens of minutes on a large org** (each entity costs a metadata
  read and a ribbon read): give it a long timeout or run it in the background.
- **Ribbon diffs come from a solution export, not the ribbon reads.** Each `ribbon_diff`
  record is the raw customization of one entity (key: its logical name; `""` is the
  application), and an entity with an empty diff has none. An org-wide run exports the
  Default solution (minutes, tens of MB on CRM 2011); Dataverse online refuses that, so
  there the diffs are rebuilt from the ribbon tables' unmanaged rows and the kind reads
  `partial` (no rules, templates or labels, no managed diff): use `--solution` for a
  whole diff. It reads `unsupported` if the tables fault. A managed solution
  cannot be exported either (a migration target is usually imported managed): each
  managed `--solution` is a `ribbon_diff` gap and the run still exits 0, and the kind
  reads `unsupported` only when every named solution is managed. An `unsupported`
  `ribbon_diff`, a managed solution's gap, or one from an older extract is unread:
  report "ribbon diffs unknown".
- **Each report is a `report` record with its data provider.** The key is the report
  name, the RDL body is its one artifact, and `fields.data_provider` is `fetch`, `sql` or
  `unknown`; Dataverse online runs only `fetch`. A Reporting Services report whose body
  the endpoint did not return reads `unknown` with a reason and one extraction gap naming
  it, while the `report` kind stays `complete`: read the gap, not the coverage. A manifest
  older than v4 reads `report` as `unsupported`: report "reports unknown", never "no reports".
- **It prints progress on stderr** (`[crm] extract stage=... done=n/total`, with an
  `eta` marked as an estimate); stdout, the JSON envelope, is unchanged. Read
  stderr to tell a slow run from a stuck one; `--quiet` silences it.
- **To inventory part of an org, narrow it to solutions** (`--solution`, see
  `--help`). The entity reads shrink to the member entities, and web resource
  content and assembly binaries are fetched for members only; the other component
  reads still run org-wide, so a narrowed run is not proportionally quicker. An
  unknown solution name is a read error on the `solution` kind (exit 1), not a
  usage error.
- **An extract can leave out stock components** (managed, or owned by a managed
  solution) and their payload reads; the manifest's `scope.system` says
  `excluded`, and edges into them show as `platform:` gaps. Stock flows down the
  plug-in chain: a type, step or image on a managed (platform or vendor) assembly
  is left out even when its own flag reads unmanaged. That includes steps the org
  registered itself on such an assembly. A managed component customized in place is missed:
  audit it and those steps with a full-scope extract, and extract both stages of a
  `diff` with the same scope, unless one side is a `--solution` run (see Comparing).
- **`--with-usage` records a per-row `usage`** on entities, plug-ins and processes.
  A row with no `usage` was not read, which differs from `state: unknown` (the
  probe could not tell); count `unknown` as unconfirmed. A failed probe leaves its
  rows `unknown`, adds a gap and a `data.errors` line, exits 1 and keeps the
  checkpoint: a re-run probes only the group that failed. `diff` compares usage
  only when both stages were extracted with it.
- **Resume by re-running the identical command on the same `--out` folder.** It
  reuses finished reads (`data.resumed`, `data.reused`) and continues paging
  after the last finished page. A folder holding a checkpoint for another org
  URL or another `--solution` scope ("holds a checkpoint of another scope"), or
  files and no checkpoint (a finished inventory included), is refused: pass a
  new folder, or repeat the original scope.
- **A failed read is continue-and-report.** The command exits 1 with `ok:false`
  but the summary still in `data` (`data.kinds`, `data.errors`,
  `data.complete: false`), and the manifest is written. Re-run on the same
  folder to retry only what failed. When a re-run returns the same
  `data.errors`, the failure is permanent (a page with no paging cookie,
  content that is not base64, a fault the server repeats): stop and report
  those errors and the kinds' coverage to the user. An auth or transport
  failure stops the run with no `data`; re-running resumes it.
- **A table deleted mid-run is a gap, not an error.** When the entity read or
  the ribbon read answers that the table no longer exists, the table is left
  out of the records and each kind it would have fed gets a gap ("no longer
  exists, deleted since the listing", and one count gap per kind for its forms and
  views); the run can still finish `complete` and
  a resume does not retry it. Any other fault on a table stays an error.
- **Judge completeness by each kind's coverage in the manifest before trusting
  record counts**, and report the manifest's `gaps` to the user as manual
  follow-up: they name what the inventory could not capture, both what the
  endpoint cannot read (assemblies on disk or in the GAC, ISV pages, external
  integrations, the shared data sources of on-premises SSRS reports), membership or dependency
  rows naming a component of a kind v1 does not record, whose read failed, or
  outside the `--solution` scope,
  and rows whose component key field the org left empty.

## Comparing inventories (`crm inventory diff`)

`crm inventory diff A B` compares two inventory folders from any stages
(CRM 2011, on-prem v9, cloud), `A` the earlier one. It reads only the folders, so
it needs no profile and no network.

- **Exit 0 means it compared, not that they match.** Read `data.counts` (per kind)
  and `data.by_origin`; report `custom` drift first, since `system` components
  differ across platform versions on their own.
- **A `--solution` side against a whole-org side is a restricted diff.** Extract the
  migrated target with `--solution` and the source at the default scope: only the
  narrowed side's keys and the source's members of the same-named solutions (a custom
  entity member with its subcomponents) are compared. Read `header.restricted`:
  `left_out` source keys were not compared (report them as out of scope), and a name in
  `missing` means the source lacks that solution, so a component only the source holds
  went unseen. A managed import reads `changed` on `origin` or `managed` alone:
  expected, report it apart.
- **The per-key evidence is in the `--out` file** (`entries`: differing fields
  with both values, artifacts with both normalized hashes, both ids, a reason):
  pass `--out` whenever you will report keys, not just counts.
- **An older extract hides column facet drift.** An `attribute` record now carries its
  type facets (`max_length`, `format`, `default_value`, `precision`, `min_value`,
  `max_value`, `option_set_global`) where its type defines them, on every platform.
  `diff` compares a facet only where both stages recorded it, so a facet missing on
  either side is unknown, not equal: re-extract that side before reporting a length or
  format as matching.
- **Only a v9 extract names entity sets.** An `entity` record from a v9 or Dataverse
  source carries `entity_set_name`, which `convert --target-inventory` reads for OData v2
  sites; a CRM 2011 extract or an older one has none (`diff` skips it where one side
  lacks it), so extract the convert target from the v9 org.
- **An inactive form in the later stage is a finding, and `diff` misses it against CRM
  2011.** A v9 extract records `active` on each `form` record (from `formactivationstate`);
  a CRM 2011 extract or an older one has none, so `diff` skips it where one side lacks it
  and an unread value never reads as "agrees". The CRM 2013 upgrade added new forms beside
  the 2011 ones and left activation to the customer. Against a 2011 source, read the later
  stage's `manifest.json` for `form` records with `fields.active: false`; between two v9
  stages the same fact is a `fields.active` change in the `--out` entries.
- **An older extract reads every ribbon `changed` once.** A ribbon's
  `normalized_sha256` now ignores the order of its rule and command siblings,
  and a manifest written before that hashed the raw text: re-extract the older
  side instead of reporting those ribbons.
- **`not_comparable` is unknown.** A kind not read `complete` on both sides
  (`data.incomplete` lists them, even with no records), or a key an extraction
  gap names with nothing else differing, cannot be judged: report it with the
  coverage or the gap as unknown, not as a removal.
- **`identity_only` is expected across a migration**: only the GUID differs,
  typical when a route re-creates components. Report it apart from `changed`.
- **`ambiguous` keys repeat on a side**: name them from the `--out` entries so
  the user resolves the duplicate.
- Report matches as evidence that the recorded customization agrees; the orgs
  may still behave differently.

## Mapping an inventory to source (`crm inventory map-source`)

`crm inventory map-source INVENTORY --repo PATH` matches an inventory's custom web
resources and plug-ins to a local checkout of the application repository. It reads
the two folders only: no profile, no network, no build.

- **Only `matched` is a match.** A web resource is matched by its normalized
  content hash, a plug-in type by its C# declaration in a project that builds the
  registered assembly. Report `stale` (the name lines up, the content differs,
  which side is newer is unknown), `ambiguous` (several files, all listed) and
  `candidate` (read its `reason`: minified or generated content, or a type in a
  project building another assembly) as leads for the user to confirm.
- **`missing_source` means not in this checkout**: the source may live in another
  repository or branch, or be lost. Ask before calling it lost.
- **Read `data.incomplete` first**: a kind the inventory did not read `complete`
  leaves components out of the map, so its `unmatched_source` files may be
  deployed after all.
- **Name files from the `--out` map** (`components`, `shared_projects`,
  `external`, `unmatched_source`), not from `data`, which carries counts only.
- `shared` web resources and shared projects change together with every
  consumer: say so when the user plans to edit one.

## Grouping an inventory into work packages (`crm inventory packages`)

`crm inventory packages INVENTORY --source-map FILE --out DIR` groups an inventory
into work packages: connected groups of components and source files that change
together. It reads files only. A source map not made by `map-source --out` from the
same inventory is refused.

- **Read `data.incomplete` first**: a kind not read `complete` is missing from the
  graph, so a package may lack members and consumers.
- **One package is one repair job.** A shared library and every form, ribbon or
  assembly using it land in one package: plan and hand it out whole.
- **Report every unknown** to the user: each package's `findings` (unknown edges:
  `dynamic`, `isv_page`, `external_url`, `unresolved_url`, `not_in_inventory`,
  `unreadable`; extraction
  gaps; doubtful source map statuses), plus `index.json` `unattached_gaps` and
  `unattached_unknowns`, the ones in no package. Each needs a human look.
- **`out_of_scope` is the exception.** On an inventory that did not read the whole
  org, a web resource name it holds no record for is not proven missing (it may be
  stock, or another solution's). Those edges are counted apart (`data.out_of_scope`,
  `unattached_out_of_scope`), not in `unknowns`: mention the count, do not report them
  as unknowns, and read the whole org when one must be settled.
- **`{"status": "unknown"}` in a field means no data, not no work.** No
  `expected_behavior` means no scenario seeds the package: ask the user for one.
- **Scenarios are a JSON file**: `{"scenarios": [{"name", "seeds": ["<kind>:<component
  key>"], "expected_behavior": ["..."]}]}`. Report `seeds_not_found` (a seed the inventory
  lacks, often a typo), `seeds_unpackaged` (a seed in no package, such as an entity) and
  `unmatched_scenarios`.
- **Read packages from `--out`**: `index.json` lists them, `<package id>.json` holds
  each; `data` is the summary. Package and verification ids hash their members, so
  they are stable across runs: refer to them by id.
- **Add the compatibility verdicts with `--findings FILE`** (the `assess --out` file of
  the same inventory; another inventory's is refused, exit 1). Blocker, deprecated,
  optional and unknown findings join the package holding their component; read each
  package's `counts` and the `compatibility.unplaced` list in `index.json`, findings no
  package holds, which still need a human look. `clear` is not merged.

## Judging recorded verification results (`crm inventory verify`)

`crm inventory verify PACKAGES --results FILE` checks each work package's
requirements against a verification results file and gives each package a verdict:
`failed`, `blocked`, `not_tested` or `complete`, the first that applies. It runs no
check and reads files only.

- **Let `verify` call a repair complete**: record a result per requirement and
  report the verdict it gives. A `pass` with no evidence, or whose evidence file is
  missing or changed, counts as not tested.
- **Record what you ran**: a results file is `{"schema_version": 1,
  "packages_sha256": "<data.packages_sha256 of the packages run>", "results":
  [{"package": "pkg-...", "requirement": "req-...", "outcome":
  "pass|fail|blocked|not_tested", "evidence": [{"path": "<relative to the results
  file>", "sha256": "..."}], "expected", "actual", "deployed": [{"component",
  "sha256"}], "errors", "environment": {"profile", "org"}, "open_decisions",
  "negative_control"}]}`. The package and requirement ids come from the packages
  output; a results file of another packages output is refused (exit 1).
- **An environment that was down is `blocked`, never `pass` or `fail`**: say what
  blocked it in `errors`.
- **A negative control** (`"negative_control": true`) records a regression you
  introduced on purpose; it must `fail`. Report every `escaped` control: the check
  missed the regression, so its package cannot be complete.
- **Report `unknown_results`**: a result whose package or requirement id the packages
  output lacks, usually a typo or results from another run.
- **Read the verdicts, not the exit code**: every verdict exits 0. The `--out`
  report's `reason` says why each requirement is not tested.

## Assessing compatibility (`crm inventory assess`)

`crm inventory assess` runs the compatibility rules shipped with crm over an
inventory, for one target, and writes a findings file.

- **Every component gets a finding**, keyed by component key: `blocker`,
  `deprecated` or `optional` from a rule hit, `unknown` with a `reason`, or `clear`
  (the rules that apply found nothing). `clear` means only that the rules that apply
  found nothing, and they cover web resource scripts, plug-in assembly
  registration, how forms and ribbon diffs call scripts and, given the source map and the
  repository, the plug-in projects, and classic dialogs (`process-dialog`, deprecated, no
  automatic conversion). A process that is not a dialog is `clear` too, which says nothing about
  its mode or trigger. Judge a `clear` component from evidence.
- **Resolve every `unknown`** before calling a component ready: `no rule` means no rule
  judges it (most kinds), and an `extraction gap` or `kind coverage` reason means the
  inventory never fully read it. A `source version` reason marks every ruled component
  of an inventory whose org version extract could not read: re-extract.
- **Quote the evidence**: a script hit carries the artifact `path`, `sha256` and match
  `locations` (line, column); a registration hit carries the `field` and `value`; a form or ribbon hit carries
  the flagged `registrations`; a plug-in project hit carries the repository `path`,
  `target_framework_version` (null: none declared, which counts as below 4.6.2),
  `sdk_references` and `fired` (`framework`, `sdk_references` or both). The
  fix and the Microsoft Learn sources of each rule hit are under `rules_hit`.
- **Read `counts.by_class`, not the exit code**: a blocker exits 0.
- **`field isolation_mode not recorded`** marks an inventory extracted by an older crm:
  re-extract to judge the isolation rules.
