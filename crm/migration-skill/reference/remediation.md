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

Run `crm inventory convert` after `assess` and `packages`; it writes only into a
new `--out` folder (`--help` has the inputs). A finding gets a patch only when its
rule names a transform. Every transform reads JavaScript web resources only (the form handler one also patches form XML), and
one file's rules all land in one patch (one ledger entry per rule). Plug-in projects
are the one C# step (see "Plug-in projects" below). Which rows of
Microsoft's client API deprecation table convert:

| Rewritten (`patched`) | Manual review, code untouched |
|---|---|
| `Xrm.Page.context.getClientUrl()` and the other same-name methods to `Xrm.Utility.getGlobalContext()` | any other use of `Xrm.Page.context`, an alias |
| `getUserName`, `getUserId`, `getUserLcid`, `getOrgLcid`, `getOrgUniqueName`, `getIsAutoSaveEnabled` to the `userSettings` or `organizationSettings` property; `getTimeZoneOffsetMinutes()` to `userSettings` | a call with arguments |
| nothing for `getUserRoles()` | every `getUserRoles()` call: roles become `{id, name}` lookups, not GUID strings, so each read of the list needs review |
| `row.getData().getEntity()` to `row.data.entity` | `getData()` or `getEntity()` alone |
| `Xrm.Mobile.offline` record methods to `Xrm.WebApi.offline` | `isOfflineEnabled`, other uses |
| no-argument `data.entity.save()` to `data.save()` (now asynchronous) | `save("saveandnew")` |
| nothing | `Xrm.Utility.openEntityForm`, `openQuickCreate`, `openWebResource`, `alertDialog`, `confirmDialog`, `isActivityType`: signature or sync behaviour changes |
| nothing | `addOnKeyPress`, `removeOnKeyPress`, `fireOnKeyPress`, `showAutoComplete`, `hideAutoComplete`: only a custom control replaces them |
| given a target inventory, a `GET` on a variable bound only to `new XMLHttpRequest()` in the same function, to `OrganizationData.svc` with a `$select`, onto `/api/data/v9.0/<set>`; the sync or async flag is kept and a generated `crm-odata-v2-adapter.js` returns the v2 response shape | a receiver bound to anything else, a `$filter` value built at run time outside a string or guid literal, a write, `$expand`, `$skip` (the Web API pages by `@odata.nextLink`), no `$select`, a `$.ajax` call, an entity or attribute missing from the target inventory (names are never pluralised), an entity record with no `entity_set_name` |
| a registered form handler gets `executionContext` and `formContext`, its `Xrm.Page.<member>` becomes `formContext.<member>`, each helper it calls (any depth) gains a `formContext` parameter that every call passes, and the form XML sets `passExecutionContext` on every registration | `Xrm.Page` in a nested callback, code no handler reaches, a helper whose name is used outside its file, a callback or recursive use, an `async` handler, a handler with another caller or an already-passing registration |
| a ribbon command function on a form gets a `primaryControl` parameter and the ribbon diff adds `PrimaryControl` as the action's first `CrmParameter` (the others keep their order) | a grid, subgrid, home page or application command, a function that is also a form handler or has a `CustomRule`, a differing command or an effective-ribbon action no diff carries as another caller, an already-passing action |

A shadowed `Xrm`, `window.Xrm` or `parent.Xrm`, an optional chain and a call that
spans lines are manual review for every row. Work from `ledger.json`:

- **`patched`** entries carry a diff per component. Review it, apply with
  `git apply`, then build and re-run `verify`; a patch is a proposal until the
  package's verification requirements pass.
- **`manual-review`** entries (file, line, rule, reason) are code the transform
  could not prove equivalent: an alias, a getUserRoles call, an HTML web resource, or a rewrite the emit gate rejected. Each is a task with its six
  fields; none is dropped, and none is patched by guessing.
- The repository diff exists only for a web resource the source map matched by
  hash. It derives from the deployed text, normalized to LF without a BOM, so
  the repository file's line endings can make `git apply` fail: normalize the
  file or apply the patched text by hand.
- **`adapters`** in `ledger.json` lists each package that needs `crm-odata-v2-adapter.js`. Upload it as a web resource
  and publish. Run convert with `--adapter-resource <name>` (the name you will upload it under; `{package}` expands per
  package) and each form that loads a patched script lists the adapter just before its first patched library, in one
  patch with any handler fix, and the entry records the name as `web_resource`. Without the flag, or where the list
  cannot be proven (adapter listed after, no patched library, packages differ), the form is a manual-review entry.
  Ribbons and HTML web resources are not registered: load the adapter there by hand.
  It returns the first page only: v2's `d.__next` has no counterpart, so a caller that follows pages needs review.
- `ledger.json`'s `call_graph` counts, per package, the helpers threaded, the `Xrm.Page` functions no
  handler reaches (`unreachable`) and the helpers a name used outside their file holds back
  (`boundary`); the last two are manual-review entries to remediate by hand.
- A component with no ledger entry had no transformable finding; it still needs
  its remediation task.

Done when every package task, every `unplaced` finding and every unknown edge in
`index.json` has its six fields or a named unknown, every `manual-review` ledger
entry has a task, and each patch builds clean in the repository.

## Plug-in projects: retarget, then build outside convert

A plug-in project is converted when the source map matched its assembly to exactly one project
and `--repo` names the checkout. Its ledger entry is `kind` `plugin_assembly`, keyed by the
assembly, one per work package, `rule` and `transform` both `csproj-sdk-net462`, the id of the `assess` blocker on the
same project. Apply the
`.patched.csproj` by copying it over the project (the diff is against LF text, so a CRLF file
refuses it).

- `patched` entries with `manual-review` lines: a `Microsoft.Xrm.Client` or
  `Microsoft.Xrm.Sdk.Workflow` reference (the `Microsoft.CrmSdk.CoreAssemblies` package carries
  neither) or an ILMerge step stays as written. Each is a task.
- A whole-file `manual-review` entry with no patch: the project is not a class library, is
  already SDK-style, or is unreadable.
- Builds happen outside convert, on the build worker. A Linux host with the .NET SDK is enough
  (a pilot built 9 of 9 converted projects that way). A failed build is a task on that project.
