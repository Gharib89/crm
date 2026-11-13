# Source mapping: tie deployed components to application code

Step 2 of the workflow, after the inventory is extracted. `crm inventory
map-source <inventory> --repo <checkout>` reads the inventory folder and the
application checkout. Keep the full map with `--out`; the summary holds counts.

**With no application repository, skip `map-source`.** An empty folder as
`--repo` only reads every component `missing_source`. Use each
component's saved artifact from the inventory, report the rest as unknowns, and
record that no repository was available. Every patch `convert` writes is then
for an inventory artifact only: `check-applied` reads each such entry
`not_checkable`, so its exit 0 proves nothing about a repository. Hand the
patched artifacts over as the deliverable and prove them in step 6.

A status is evidence about files, never proof the deployed build equals the
repository's. Report each custom web resource, plug-in assembly and plug-in type
with its status and act on it:

| Status | What to do |
|---|---|
| `matched` | Use the listed files as the component's source reference. |
| `stale` | Ask which side is authoritative (a hotfix on the org, or a branch not yet merged), and hold patches on this file until answered. |
| `ambiguous` | Ask the user to pick one of the listed files, and record the pick beside the package. |
| `candidate` | Needs human review; its `reason` says why. Treat it as unmatched, and cite its files as a source reference only after a person confirms them. |
| `missing_source` | The repository does not hold it. Use the inventory's saved artifact when it kept one, otherwise report an unknown. |

## Read the rest of the map

- **`shared: true`** on a component marks a web resource several components
  reference, and **`shared_projects`** lists C# projects two plug-in projects
  reference. A change to either touches every consumer: make it once, in the
  work package that holds them (`reference/remediation.md`).
- **`external`** is NuGet packages and scripts from other hosts, outside the
  application's code; note them as dependencies.
- **`unmatched_source`** is deployable repository files nothing matched. They
  may be dead code or belong to another org: list them and keep them.
- **`incomplete`** names inventory kinds that were not `complete`; the map is
  incomplete for those kinds.

Re-run `map-source` whenever the inventory or the checkout changes: the map
feeds `packages`, so a stale map yields stale packages.

Done when every custom web resource, assembly and type carries a status, and
each `stale`, `ambiguous`, `candidate` and `missing_source` entry has a recorded
decision or an open question.
