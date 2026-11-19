# Migration inventory: map to source, package, verify

All three read files only and need no profile: an inventory from `crm inventory extract`
(`reference/inventory.md`), and the outputs of the step before.

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
  package holds, which still need a human look, and `compatibility.uncovered_kinds`,
  the kinds no rule covers with their record counts. `clear` is not merged. A blocker or
  deprecated finding on a process is the one kind that creates a package (a package of one
  for an isolated dialog), so `verify` judges that process too.

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
  "sha256"}], "errors": ["<text>"], "environment": {"profile", "org"},
  "open_decisions": ["<text>"], "negative_control"}]}`; `errors` and
  `open_decisions` are lists of strings, and a bare string is refused. The package and requirement ids come from the packages
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
