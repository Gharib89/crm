# Remediation: from work packages to small patches

Steps 4 and 5 of the workflow. `crm inventory packages <inventory> --source-map
<map> --findings <assess file> --out <folder>` is offline and writes
`index.json` plus one `<package id>.json` per package. Passing `--findings`
merges each blocker, deprecated, optional and unknown finding into the package
holding its component; without it a package lists only its unknowns. Findings
never change which packages exist. Run it once per target, from that target's
findings file. A findings file made from another inventory is refused.

## Read the output

The summary gives package counts, the `compatibility` counts and the
`packages_sha256` that verification names (`reference/verification.md`). In the
package folder:

- **A package** is a connected group of components, so a shared library and its
  consumers are one package. Its file holds `source`, `consumers`, `findings`,
  `edges`, `expected_behavior` and `verification`.
- **`unplaced`** counts findings whose component sits in no package (an entity,
  a system web resource). They are not dropped: list each as its own task or a
  manual-review entry.
- **An unknown edge** is a reference the graph could not resolve (a dynamic
  name, an ISV page, another host's URL). It is an unknown, never a pass.
- **`expected_behavior`** reads `unknown` until a scenario seeds the package;
  ask the user for the expected behavior before tests are written.

## Write one task per finding, six fields each

Build the task from the package file, then fill what the file cannot supply:

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

A task missing a field names it as an unknown; it does not guess one.

## Patch small, build, then deploy

- One task per patch, one concern per diff, so each change reviews on its own
  and traces to one package task.
- A shared library or project changes once, in its package; its consumers are
  checked, not edited again.
- Run the repository's own build and checks on the patched tree before anything
  is deployed. Deployment to the target follows `reference/verification.md`.
- Re-run `assess` and `packages` after a patch lands in a new extraction. The
  `packages_sha256` changes with the inventory, and a results file recorded
  against the old one is refused.

Done when every package task carries all six fields, the `unplaced` findings
have tasks or entries, and each patch builds clean in the repository.
