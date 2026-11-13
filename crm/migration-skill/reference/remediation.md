# Remediation: from work packages to small patches

Steps 4 and 5 of the workflow. Run `crm inventory packages <inventory>
--source-map <map> --findings <assess file> --out <folder>` once per target,
from that target's findings file, so each package carries its compatibility
findings.

## Read the output

The summary gives the package counts, the `compatibility` counts and the
`packages_sha256` that verification names (`reference/verification.md`). In the
package folder:

- **A package file** (`<package id>.json`) holds `source`, `consumers`,
  `findings`, `edges`, `expected_behavior` and `verification`. A shared library
  and its consumers are one package.
- **`index.json`** lists the packages and everything no package holds:
  `compatibility.unplaced` (findings whose component sits in no package) and the
  unknown edges from nodes no package holds. List each as its own task or a
  manual-review entry; none is dropped.
- **`usage`** on a package or finding is not read when absent, and stays under
  every filter. A derived `unused` (an entity with no records) is unconfirmed:
  the data may have been deleted or migrated. A package or finding dropped by
  `--in-use` or `--exclude-unknown` is unassessed, its blockers with it: name
  the flags and the summary's `usage_filtered` in the report.
- **An unknown edge** inside a package is a reference the graph could not resolve
  (a dynamic name, an ISV page, another host's URL). It is an unknown, never a
  pass.
- **`expected_behavior`** reads `unknown` until a scenario seeds the package.
  Ask the user for the expected behavior, put each answer in a scenarios file
  and re-run `packages` with `--scenarios`, before tests are written.

## Write one task per finding, six fields each

1. **Component and source references**: the `source` entries. A `candidate`,
   `stale` or `missing_source` reference is named as such, with its open
   question (`reference/source-mapping.md`).
2. **Affected consumers**: the package's `consumers`.
3. **Explanation and documentation**: the finding's reason, its evidence and the
   Microsoft Learn link from its `sources`.
4. **Proposed transformation**: the smallest change that removes the finding.
5. **Confidence and unknowns**: how sure the change is and what would change
   that, including any unknown edge in the package.
6. **Tests needed**: the package's `verification` requirement ids and the kind of
   test each needs.

A task missing a field names it as an unknown.

## Patch small, build, then deploy

- One task per patch, one concern per diff, so each change reviews on its own
  and traces to one package task.
- A shared library or project changes once, in its package; its consumers are
  checked, not edited again.
- Run the repository's own build and checks on the patched tree before anything
  is deployed. Deployment to the target follows `reference/verification.md`.
- A new extraction changes `packages_sha256`, and a results file recorded
  against the old one is refused: re-run `map-source`, `assess` and `packages`
  from the new inventory.

## Convert code that a rule can rewrite

Run `crm inventory convert` after `assess` and `packages`. The `crm` skill's
convert page (`inventory-convert.md`) owns which calls it rewrites and how
`ledger.json` reads. Work from the ledger:

- **A `patched` entry is a proposal** until the package's verification
  requirements pass. Review it, apply it and prove the tree carries it (below),
  then build.
- **Each `manual-review` entry is a task**, the `unreachable` and `boundary`
  helpers of `call_graph` included: none is dropped, and none is patched by
  guessing.
- **Each package under `adapters`** needs the adapter uploaded and published
  before its patched scripts run.
- A component with no ledger entry still needs its remediation task.

Done when every package task, every `unplaced` finding and every unknown edge in
`index.json` has its six fields or a named unknown, and every `manual-review`
ledger entry has a task.

## The loop after convert

Convert and `check-applied` never write to the repository or an org: the steps
that do are yours, each behind its gate. Do them in order and stop at the first
gate that fails.

1. Review each diff. Gate: you read it.
2. `git apply --check`, then `git apply`, in the repository. Gate: the check
   passes; a failed check means re-run `map-source` and convert again, or edit by hand.
3. `crm inventory check-applied`. Gate: exit 0 and `checked` above 0. A pass with
   `checked` 0 proves nothing (every entry was `not_checkable`; `nothing_checked`
   is `true` when the ledger held a patch): re-run `map-source` against the
   application repository and convert again, or, with no repository, record that
   nothing was proven and treat each patch as unverified. A `diverged` file means
   re-run convert from a fresh extract; `not_checkable` rows stay tasks.
4. Build and check in the repository's own toolchain. Gate: green.
5. Continue in `reference/verification.md`: confirm the target, deploy, re-extract,
   `map-source`, record results, `verify`.

## Plug-in projects: retarget, then build outside convert

`convert --repo` retargets the project of a plug-in assembly the source map
matched to exactly one project; apply the `.patched.csproj` by copying it over the project. Each
`manual-review` line on its entry (a reference the new package does not carry, an
ILMerge step) and each whole-file `manual-review` entry is a task. Builds happen
outside convert, on the build worker; a failed build is a task on that project.
