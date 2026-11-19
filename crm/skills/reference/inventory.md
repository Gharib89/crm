# Migration inventory: extract, compare

`extract` reads an org, a `crm2011` profile included (`reference/setup.md`, Legacy
CRM 2011 orgs); `diff` reads files only and needs no profile. Mapping to source, work
packages and verification (`map-source`, `packages`, `verify`) are in
`reference/inventory-packages.md`; compatibility findings and the readiness roll-up
(`assess`, `readiness`) in `reference/inventory-assess.md`; code conversion and its check
(`convert`, `check-applied`) in `reference/inventory-convert.md`.

## Extracting an inventory (`crm inventory extract`)

A migration inventory is a read-only, descriptive record of an org's customizations: a
`manifest.json` (schema v6, which also reads v1 to v5: records keyed by component key,
per-kind coverage, extraction gaps) plus raw artifacts (XML, XAML, web resource content, assembly
binaries). It describes what is deployed; it is never a spec for `crm apply`.

- **It runs for tens of minutes on a large org** (each entity costs a metadata
  read and a ribbon read): give it a long timeout. A run in the background of an agent's
  session ends with that session, so start it detached from the session (`nohup`, `setsid`
  or a terminal multiplexer).
- **Ribbon diffs come from a solution export, not the ribbon reads.** Each `ribbon_diff`
  record is the raw customization of one entity (key: its logical name; `""` is the
  application), and an entity with an empty diff has none. An org-wide run exports the
  Default solution (minutes, tens of MB on CRM 2011); the Web API (online and on-prem v9)
  refuses that, so there the diffs are rebuilt from the ribbon tables' unmanaged rows and
  the kind reads `partial` (no rules, templates or labels, no managed diff): use
  `--solution` for a whole diff. It reads `unsupported` if the tables fault. A managed
  solution cannot be exported either (a migration target is usually imported managed):
  each managed `--solution` is a `ribbon_diff` gap and the run still exits 0, and the kind
  reads `unsupported` only when every named solution is managed. An `unsupported`
  `ribbon_diff`, a managed solution's gap, or one from an older extract is unread:
  report "ribbon diffs unknown".
- **Each report is a `report` record with its data provider.** The key is the report
  name, the RDL body is its one artifact, and `fields.data_provider` is `fetch`, `sql` or
  `unknown`; Dataverse online runs only `fetch`. A Reporting Services report whose body
  the endpoint did not return reads `unknown` with a reason and one extraction gap naming
  it, while the `report` kind stays `complete`: read the gap, not the coverage. A manifest
  older than v4 reads `report` as `unsupported`: report "reports unknown", never "no reports".
- **Security and configuration kinds carry their child rows.** `security_role` (key: name,
  root business unit only, `fields.privileges` maps each privilege to its depth mask),
  `field_security_profile` (name; `fields.permissions` by `<entity>.<attribute>`),
  `duplicate_rule` (base entity and name; `fields.conditions`), `email_template` (template
  type and title; the body is its artifact) and `connection_role` (name). A changed
  privilege depth, permission or condition reads `changed` in a diff. On CRM 2011 and
  on-prem v9 the rule table has no `ismanaged`, so a default-scope run keeps every
  duplicate rule (a "carry no ismanaged" gap). `packages` puts each kind's records in one
  package of their own. A manifest older than v5 reads all five `unsupported`: report them
  unknown, never absent.
- **Application lifecycle kinds exist on v9 and online only.** `sla` (key: name),
  `app_module` (unique name), `alternate_key` (entity and key schema name;
  `fields.columns`), `environment_variable` (schema name; the default value only, never
  the org's current one) and `connection_reference` (logical name). Alternate
  keys come with their entity's metadata, so `--solution` keeps them like attributes. A
  `crm2011` run, or a manifest older than v6, reads all five `unsupported`: report them
  unknown, never absent. `packages` puts each kind's records in one package.
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
  solution, unless the `Active` solution owns them) and their payload reads; the
  manifest's `scope.system` says `excluded`, and edges into them show as `platform:`
  gaps. Stock flows down the plug-in chain: a type, step or image on a managed
  (platform or vendor) assembly is left out even when its own flag reads unmanaged.
  That includes steps the org registered itself on such an assembly. A managed
  component customized in place is kept when it moved to the `Active` solution (CRM
  2011 does this); one that stayed under its managed owner is missed: audit it and
  those steps with a full-scope extract, and extract both stages of a `diff` with the
  same scope, unless one side is a `--solution` run (see Comparing).
- **`--with-usage` records a per-row `usage`** on entities, plug-ins and processes.
  A row with no `usage` was not read, which differs from `state: unknown` (the
  probe could not tell); count `unknown` as unconfirmed. A failed probe leaves its
  rows `unknown`, adds a gap and a `data.errors` line, exits 1 and keeps the
  checkpoint: a re-run probes only the group that failed. `diff` compares usage
  only on rows both stages read it for.
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
- **Only a v9 extract records entity sets and write metadata.** An `entity` record from
  a v9 or Dataverse source carries `entity_set_name`, which `convert --target-inventory`
  reads for OData v2 sites; for an OData v2 write it also reads the write metadata: each
  `attribute` record's `schema_name` and `date_behavior` and each `relationship`
  record's `navigation_property`. A CRM 2011 extract or an older one has none (`diff`
  skips each where one side lacks it), so extract the convert target from the v9 org,
  and re-extract a target whose `attribute` records lack `schema_name`, whose date
  attributes lack `date_behavior` or whose one-to-many relationships lack
  `navigation_property`.
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
