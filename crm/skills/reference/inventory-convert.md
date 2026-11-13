# Migration inventory: convert code, check it applied

Both verbs read files only, need no profile, and never write to the repository or
an org. Assess, whose findings convert reads, is in `reference/inventory-assess.md`;
the `crm-migration` skill, when installed, says how a migration plan works the
ledger.

## Converting code a rule can rewrite (`crm inventory convert`)

Run `crm inventory convert` after `assess` and `packages`; it writes only into a new
`--out` folder (`--help` has the inputs). A finding gets a patch only when its rule
names a transform. Every transform reads JavaScript web resources only (the form
handler one also patches form XML), and one file's rules all land in one patch (one
ledger entry per rule). Plug-in projects are the one C# step (below). Which rows of
Microsoft's client API deprecation table convert:

| Rewritten (`patched`) | Manual review, code untouched |
|---|---|
| `Xrm.Page.context.getClientUrl()` and the other same-name methods to `Xrm.Utility.getGlobalContext()` | any other use of `Xrm.Page.context`, an alias |
| `getUserName`, `getUserId`, `getUserLcid`, `getOrgLcid`, `getOrgUniqueName`, `getIsAutoSaveEnabled` to the `userSettings` or `organizationSettings` property; `getTimeZoneOffsetMinutes()` to `userSettings` | a call with arguments |
| `getServerUrl()` on `Xrm.Page.context`, `Xrm.Utility.getGlobalContext()` or `GetGlobalContext()` to `getClientUrl()` (the 2011 equivalence is the rule's judgment: prove it with `inventory verify`) | any other receiver, a `GetGlobalContext` the file defines itself, a call with arguments |
| nothing for `getUserRoles()` | every `getUserRoles()` call: roles become `{id, name}` lookups, not GUID strings, so each read of the list needs review |
| `row.getData().getEntity()` to `row.data.entity` | `getData()` or `getEntity()` alone |
| `Xrm.Mobile.offline` record methods to `Xrm.WebApi.offline` | `isOfflineEnabled`, other uses |
| no-argument `data.entity.save()` to `data.save()` (now asynchronous) | `save("saveandnew")` |
| nothing | `Xrm.Utility.openEntityForm`, `openQuickCreate`, `openWebResource`, `alertDialog`, `confirmDialog`, `isActivityType`: signature or sync behaviour changes |
| nothing | `addOnKeyPress`, `removeOnKeyPress`, `fireOnKeyPress`, `showAutoComplete`, `hideAutoComplete`: only a custom control replaces them |
| nothing | every reference to the 2011 `Organization.svc` or `Discovery.svc` endpoint: each SOAP site needs a hand rewrite to the Web API (a synchronous call becomes asynchronous, with another response shape); a file whose only match is a comment, or an HTML page, gets one whole-file `manual-review` entry |
| given a target inventory, in a script web resource or an HTML page's inline script, a `GET` on a variable bound only to `new XMLHttpRequest()` in the same function, to `OrganizationData.svc` with a `$select`, onto `/api/data/v9.0/<set>`; the sync or async flag is kept and a generated `crm-odata-v2-adapter.js` returns the v2 response shape | a receiver bound to anything else, a `$filter` value built at run time outside a string or guid literal, a write, `$expand`, `$skip` (the Web API pages by `@odata.nextLink`), no `$select`, a `$.ajax` call, an entity or attribute missing from the target inventory (names are never pluralised), an entity record with no `entity_set_name` (a CRM 2011 or older extract records none: re-extract the v9 target) |
| a registered form handler gets `executionContext` and `formContext`, its `Xrm.Page.<member>` becomes `formContext.<member>`, each helper it calls (any depth) gains a `formContext` parameter that every call passes, and the form XML sets `passExecutionContext` on every registration | `Xrm.Page` in a nested callback, code no handler reaches, a helper whose name is used outside its file, a callback or recursive use, an `async` handler, a handler with another caller or an already-passing registration |
| a ribbon command function on a form gets a `primaryControl` parameter and the ribbon diff adds `PrimaryControl` as the action's first `CrmParameter` (the others keep their order) | a grid, subgrid, home page or application command, a function that is also a form handler or has a `CustomRule`, a differing command or an effective-ribbon action no diff carries as another caller, an already-passing action |

A shadowed `Xrm`, `window.Xrm` or `parent.Xrm`, an optional chain and a call that
spans lines are manual review for every row. Read `ledger.json`:

- **`patched`** entries carry a diff per component: a proposal to review and apply,
  never proof the component works.
- **`manual-review`** entries (file, line, rule, reason; `snippet`, `replacement` and
  `return_use` where known) are code the transform could not prove equivalent: an
  alias, a getUserRoles call, an HTML web resource the OData transform does not read,
  or a rewrite the emit gate rejected. `snippet` is the statement at the site,
  `replacement` the rule's documented fix, so finish the edit from them rather than
  rediscovering the constraint. On a `getUserRoles()` entry, `return_use` says how the
  list is read: `assigned`, `returned` and `argument` need the value's later reads
  traced; `member` needs each read checked against the `{id, name}` lookups (a GUID
  comparison from `.indexOf(id)` or `[0]` changes to `.id`; a `.length` count does
  not); `discarded` can be deleted; `other` needs the site read by hand and each use
  traced as for `assigned`.
- The repository diff exists only for a web resource the source map matched by hash. It
  derives from the deployed text, normalized to LF without a BOM, so the repository
  file's line endings can make `git apply` fail: normalize the file or apply the patched
  text by hand.
- **`adapters`** lists each package that needs `crm-odata-v2-adapter.js`. Upload it as a
  web resource and publish. Run convert with `--adapter-resource <name>` (the name you
  will upload it under; `{package}` expands per package) and each form that loads a
  patched script lists the adapter just before its first patched library, in one patch
  with any handler fix, and the entry records the name as `web_resource`. An HTML page
  gets an adapter `<script src>` just before its first rewritten script, a patched
  library or a rewritten inline script, in one patch. Without the flag, or where the
  registration cannot be proven (adapter listed after, no patched script, packages
  differ, the page's script tags unreadable), the form, page or ribbon command is a
  manual-review entry and the page's inline rewrite is dropped with it, because a
  rewritten call without the adapter throws. A ribbon command gets an action calling
  `CrmODataV2.load` from the adapter just before its first patched action, one per
  command, in one patch with any `PrimaryControl` fix. A `CustomRule` that calls a
  patched library is not patched (a rule runs at render, before any action): its
  manual-review entry names the rule and its commands, so load the adapter for it by
  hand. On an on-prem target, run a
  patched ribbon command to prove it loads the adapter. The adapter returns the first
  page only: v2's `d.__next` has no counterpart, so a caller that follows pages needs
  review.
- **`call_graph`** counts, per package, the helpers threaded, the `Xrm.Page` functions
  no handler reaches (`unreachable`) and the helpers a name used outside their file
  holds back (`boundary`); the last two are manual-review entries.
- A component with no ledger entry had no transformable finding.

### Plug-in projects

A plug-in project is converted when the source map matched its assembly to exactly one
project and `--repo` names the checkout. Its ledger entry is `kind` `plugin_assembly`,
keyed by the assembly, one per work package, `rule` and `transform` both
`csproj-sdk-net462`, the id of the `assess` blocker on the same project. Apply the
`.patched.csproj` by copying it over the project (the diff is against LF text, so a CRLF
file refuses it).

- `patched` entries with `manual-review` lines: a `Microsoft.Xrm.Client` or
  `Microsoft.Xrm.Sdk.Workflow` reference (the `Microsoft.CrmSdk.CoreAssemblies` package
  carries neither) or an ILMerge step stays as written.
- A whole-file `manual-review` entry with no patch: the project is not a class library,
  is already SDK-style, or is unreadable.
- The build stays outside convert. A Linux host with the .NET SDK is enough (a pilot
  built 9 of 9 converted projects that way).

## Checking the repository carries the patches (`crm inventory check-applied`)

`crm inventory check-applied <convert out> --repo <checkout>` is the gate between
applying the patches and building. It gives each ledger entry that patches a repository
file one status, from the file's normalized hash (no BOM, LF):

- `applied`: the file equals the patched text.
- `not_applied`: it equals the original: the patch was left out.
- `diverged`: it equals neither: the file was edited since extraction; re-run convert
  from a fresh extract.
- `missing`: the file is not there (moved or deleted).
- `not_checkable`: no repository file (a manual-review entry, or a patch for an
  inventory artifact only); it does not affect the exit code.

Exit 0 means every checkable entry is `applied`; 1 otherwise.
