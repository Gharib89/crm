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
- **`usage`** on a package (and on each `assess` finding) says whether anyone
  runs it: `state`, the `signal` that decided it and a `reason`. A manifest read
  with `--with-usage` gives runtime counts; any manifest gives derived ones (a
  web resource a form references is `used`, an unreferenced one `unknown`, a form
  of an entity with no records `unused` at low confidence). No `usage` means not
  read, never unused. Pass `--in-use` to `packages` or `assess` to drop `unused`
  work from the plan, `--exclude-unknown` to drop `unknown` as well, and
  `--sort usage` to start with what is used. Say which flags were used, since a
  dropped row is not assessed.
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

Done when every package task, every `unplaced` finding and every unknown edge in
`index.json` has its six fields or a named unknown, and each patch builds clean
in the repository.
