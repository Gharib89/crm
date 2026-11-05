# Source mapping: tie deployed components to application code

Step 2 of the workflow, after the inventory is extracted. `crm inventory
map-source <inventory> --repo <checkout>` is offline: it reads the inventory
folder and the application checkout, builds nothing and touches no org. Pass
`--out` to keep the full map; the summary holds counts only.

A status is evidence about files, never proof the deployed build equals the
repository's. Report each custom web resource, plug-in assembly and plug-in type
with its status and act on it as below.

| Status | What it means | What to do |
|---|---|---|
| `matched` | Content hash equal (web resource) or C# declaration found in a project building that assembly | Use the listed files as the component's source reference. |
| `stale` | One file lines up by name, content differs, and which side is newer is unknown | Ask which side is authoritative (a hotfix on the org, or a branch not yet merged). Patch nothing until answered. |
| `ambiguous` | Several files line up; all are listed | Ask the user to pick one, and record the pick beside the package. |
| `candidate` | A minified or generated file, a type in a project building another assembly, or an assembly's project without the type; `reason` says which | Needs human review. Never treat a `candidate` as `matched`, and never cite its files as the source reference until a person confirms. |
| `missing_source` | No repository file lines up | The repository does not hold it. Use the inventory's saved artifact when it kept one, otherwise report an unknown; do not reconstruct from memory. |

## Read the rest of the map

- **`shared`** lists web resources several components reference, and
  **`shared_projects`** lists C# projects two plug-in projects reference. A
  change to either touches every consumer: make it once, in the work package
  that holds them (`reference/remediation.md`).
- **`external`** is NuGet packages and scripts from other hosts. They are not
  the application's to change; note them as dependencies.
- **`unmatched_source`** is deployable repository files nothing matched. They
  may be dead code or belong to another org. List them; delete nothing.
- **`incomplete`** names inventory kinds that were not `complete`. A map over an
  incomplete inventory is incomplete for those kinds.

Re-run `map-source` whenever the inventory or the checkout changes: its map is
the input the packages step hashes, so a stale map yields stale packages.

Done when every custom web resource, assembly and type carries a status, and
each `stale`, `ambiguous`, `candidate` and `missing_source` entry has a recorded
decision or an open question.
