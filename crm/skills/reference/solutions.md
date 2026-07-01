# Solutions — lifecycle, packager, validate, drift

Create, version, export/import solutions; bridge to `pac solution` for source
control; validate zips offline; detect component drift. Group: `solution`.
Flags/choices: `crm solution --help`.

## Solution scaffolding — publisher + solution

```bash
crm --json solution create-publisher --name crmworx --display CRMWorx \
    --prefix cwx --option-value-prefix 30000 --if-exists skip

crm --json solution create --name CRMWorx --publisher crmworx --if-exists skip
```

With a named profile active, `create-publisher` auto-wires `publisher_prefix` back into
it (pass `--no-set-default` to opt out), which only sets the schema-name prefix used to
derive column names. It does **not** set a target solution: every customization write
(`metadata create-*`, `apply`, `webresource`, `form`, …) requires its own explicit
`--solution <unique_name>` — there is no profile default and no opt-out (`--solution
Default` for a deliberate Default-Solution-only write).

Bump the version (or friendly name / description) of an **unmanaged** solution before
exporting — at least one field is required, `--version` is validated as 4-part dotted
numeric pre-HTTP, and managed solutions / patches are rejected client-side:

```bash
crm --json solution set-version CRMWorx --version 1.0.1.0
crm --json solution set-version CRMWorx --friendly-name "CRM Worx" --description "RC build"
```

Add or remove an existing component to/from an **unmanaged** solution
(`AddSolutionComponent` / `RemoveSolutionComponent`). `--type` takes a `componenttype`
integer or a friendly name (case- and separator-insensitive: `entity`=1, `attribute`=2,
`relationship`=3, `optionset`=9, `entityrelationship`=10, `role`=20, `webresource`=61, …; raw int
for anything else). Both refuse managed targets. `remove-component` is destructive
(`--yes` required). Gotcha: `add-component --type entity` with required-components on
(the default) may silently pull required components into the solution beyond the one
you asked for — the server does not report them (the CLI emits a `meta.note` reminder):

```bash
crm --json solution add-component --solution CRMWorx --type webresource --id <guid>
crm --json solution remove-component --solution CRMWorx --type 61 --id <guid> --yes
```

## Export a solution

```bash
crm solution list --unmanaged
crm solution info MyCustomSolution            # inspect one solution by unique name
crm solution export MyCustomSolution -o /tmp/snap.zip
# returns {"output": "/tmp/snap.zip", "bytes": 123456, "managed": false, ...}
```

## Import a solution

```bash
crm solution import /tmp/snap.zip --yes
# --no-overwrite keeps existing unmanaged customizations; --no-publish suppresses workflow activation
```

**Gotcha:** `solution import` **OVERWRITES** unmanaged customizations in the target org by
default, and **activates imported workflows** (`PublishWorkflows` — not `PublishAllXml`).
Pass `--no-overwrite` to keep existing customizations, or `--no-publish` to suppress
workflow activation. Both are the off-halves of boolean pairs (`--overwrite/--no-overwrite`,
`--publish/--no-publish`). Omitting `--yes` in a non-interactive context aborts.

**Gotcha — product-update dependency block.** If the server rejects the import before
processing any components due to a product-update dependency check, pass
`--skip-dependency-check` to set `SkipProductUpdateDependencies` in the request body and
allow the import to proceed past it. Applies to both the async and the on-prem
synchronous-fallback path.

**Gotcha — version ceiling (cloud→on-prem).** A managed zip carries the *package version*
of the org it was exported from, and an org **rejects any zip newer than itself**. A
solution exported from Dataverse online (v9.2) fails to import into on-prem v9.1 with
`0x80048068` / HTTP 400 ("you can only import solutions with a package version of 9.1 or
earlier into this organization"). Promotion must travel **same-or-lower version** — build
on the lowest version in your dev→test→prod chain, or keep all tiers on one platform. This
is distinct from the API-version cap (a `v9.2` *request* → HTTP 501); here it's the
*solution* package version, not the endpoint. `solution validate --against-org` catches
this before the import (it compares `SolutionPackageVersion` against `RetrieveVersion()` —
see "Validate a solution zip before import" below), so pre-flight against the **target**.

**Gotcha:** importing a security **role** from a **managed** solution. On on-prem v9.x
this strips all *manually added* privileges of that role on the target org (privilege-level
*changes* survive; manual *additions* are removed); newer Dataverse online instead *merges*
role privileges and keeps them — per Microsoft Learn, "how managed solutions are merged →
merge security role privileges". Either way, mitigate by managing every update to a given
role from the **same** custom solution; never add privileges directly on the target org,
and don't move a role's updates to a different solution.

The result carries `import_job_id` and `async_operation_id` — capture both; they
drive the investigation workflow below.

## Managed-solution upgrade lifecycle — `clone-as-patch` / `stage-and-upgrade` / `apply-upgrade` / `uninstall`

First-class verbs wrapping the `CloneAsPatch`, `ImportSolution` (`HoldingSolution`),
`DeleteAndPromote`, and solution-delete server actions — so you never drop to
`crm action invoke CloneAsPatch/DeleteAndPromote` (agent-infeasible). They all
work on **both** on-prem v9.x and Dataverse online.

```bash
# Small hotfix on top of a parent: clone a patch (version revision auto-bumps from the parent)
crm --json solution clone-as-patch --solution CRMWorx
# → {cloned, parent_solution, version, patch_solutionid}

# Major upgrade: stage the new managed zip as a HOLDING solution (not yet live)…
crm solution stage-and-upgrade /tmp/crmworx_2_0.zip --yes
# …then apply it (DeleteAndPromote replaces the base + its patches) — one-shot:
crm solution stage-and-upgrade /tmp/crmworx_2_0.zip --promote --solution CRMWorx --yes
# …or promote a separately-staged holding solution later (after verifying it):
crm solution apply-upgrade CRMWorx --yes

# Remove a solution (managed base also removes its patches, server-side)
crm solution uninstall --solution CRMWorx --yes
```

**Workflow:** a holding import alone does **not** make the upgrade live — it stages
and validates. Either promote in the same call (`stage-and-upgrade --promote`) or
stage now and run `apply-upgrade <name>` later — both fire the same `DeleteAndPromote`
(the destructive step that deletes the old base). The decoupled shape lets you stage,
verify with `solution import-result <id>`, then promote separately.

**Gotchas:**
- `clone-as-patch` auto-bump increments the **revision** (4th part); a patch must
  keep the parent's `major.minor`. Pass `--version` for a specific build/revision.
- `stage-and-upgrade` reuses the `solution import` pipeline — same per-component
  result parsing, the on-prem synchronous-`ImportSolution` fallback, and
  `meta.warnings` on a partial failure all apply.
- `uninstall` **pre-checks** `RetrieveDependenciesForUninstall` and refuses (no
  DELETE) when blockers exist; `--force` skips the check. Inspect blockers first
  with `solution dependencies <name>`.
- All three are gated destructive verbs (`--yes`) except `clone-as-patch` (a
  create). `--dry-run` previews every one without mutating.

## Translate display labels — `crm translation export` / `import`

Translation is **solution-scoped** (the `ExportTranslation` / `ImportTranslation`
actions take a solution, not an entity) — to translate one entity, put it in a
solution. Dual-target: on-prem v9.x and Dataverse online.

```bash
crm --json translation export --solution CRMWorx -o labels.zip
# labels.zip = CrmTranslations.xml (Excel-openable spreadsheet) + [Content_Types].xml;
# translator adds a column per language code (e.g. 1034) and fills it in

crm --json translation import labels.zip --yes            # re-zipped edited files, NOT the bare XML
crm --json solution publish-all                           # labels do NOT surface until published

# One-step: import + publish in a single call (PublishAllXml after import)
crm --json translation import labels.zip --yes --publish
```

`--publish` runs `PublishAllXml` immediately after a successful import, so
labels surface without a separate `solution publish-all` step. Default off —
omitting it leaves the existing behavior (import only, `meta.warnings` publish
reminder). Under `--dry-run` the import is previewed and publish is skipped.

Gotchas: import fails on any translated string **>500 chars**; labels for
languages **not provisioned** on the target are discarded with a warning;
customization happens only in the base language. The import envelope carries
`import_job_id` (results via `crm solution import-result <id>`) and a
`meta.warnings` publish reminder.

Do **not** reach for `crm action invoke ExportTranslation` — it returns the zip
as a base64 blob inside the JSON body (manual decode + unpack); the
`translation` verbs are that plumbing. The GUI alternatives are the native
**Translations → Export/Import translations** menu and XrmToolBox
**Easy Translator** (community tool, on-prem + online).

## Source control — `pac solution` bridge

Put a solution under source control with the offline `pac solution unpack`/`pack`
bridge (no connection/profile needed; resolves the exe via `--pac-path` →
`CRM_PAC` → PATH — the old `--solutionpackager-path`/`CRM_SOLUTIONPACKAGER` names
still work as deprecated aliases — else errors pointing at the cross-platform
Power Platform CLI install — `dotnet tool install --global Microsoft.PowerApps.CLI.Tool`).
**`git diff` on the extracted tree IS the solution diff:**

```bash
crm solution extract --zipfile /tmp/snap.zip --folder src/MyCustomSolution
# ...commit + review git diff, then rebuild a zip from the tree
crm solution pack --zipfile dist/built.zip --folder src/MyCustomSolution
# a non-zero pac exit fails the command;
# envelope: {action, exit_code, folder, zipfile, stdout_tail}
```

## Validate a solution zip before import (CI gate)

Offline static analysis — no connection or profile needed:

```bash
crm solution validate /tmp/snap.zip
# checks: RootComponents<->customizations parity, $webresource: ribbon refs,
# global option-set bindings, well-formed XML, required members present.
# exits non-zero on any error-severity finding.
```

Add `--against-org` to also check for colliding `formid`/`savedqueryid` GUIDs,
BPF process-stage GUIDs (`StageId`/`NextStageId` in `Workflows/*.xaml`, probed
against `processstages` — the `CreateProcessStage` duplicate-key class),
existence of referenced web resources and global option sets, **and the
package-version ceiling** in the target org (requires a connection/profile). Use
before `solution import`.

**`--against-org` now enforces the version ceiling.** It reads the package's
`SolutionPackageVersion` and compares it to the org version (`RetrieveVersion()`):
a package newer than the org (even a newer minor) is an **error** finding —
import would fail with `0x80048068`. So a green `--against-org` validate now
clears that ceiling, not just GUID/ref collisions. The check is best-effort —
an absent/unparseable package version or an unreadable org version degrades to a
warning/skip and never falsely flips the report invalid; the offline (no
`--against-org`) run is still purely structural and does **not** check it. And
run `--against-org` only against the **import target**, never the export source —
validating against the source always reports `formid`/`savedqueryid` collisions,
because those components already live there.

## Investigating a failed import

Work the timeline in order — gate, monitor, post-mortem, verify:

1. **Before** — `solution validate <zip>`, plus `--against-org` against the
   target (previous section). Offline findings are cheaper than a failed import.
2. **During** — `crm --json solution job-status <id>` (alias for
   `crm async get`) polls the import's async operation; the id is the import
   envelope's `async_operation_id`. Didn't capture it? `crm --json async list`
   finds the operation.
3. **After** — `crm --json solution import-result <id>` re-fetches the
   ImportJob of any prior import and parses per-component pass/fail outcomes;
   the id is the import envelope's `import_job_id` — present **once the import job
   starts**. A pre-execution rejection (the version-ceiling gate above, or a declared
   missing dependency that fires at entry) returns an *error* envelope with no job id and
   `import-result` 404s — that case is "rejected before execution", not
   "executed-but-failed". Parsing is best-effort: a missing or unparseable ImportJob data
   column degrades to a `meta.warnings` note, never an error.

**On-prem caveat.** On-prem orgs import via the synchronous `ImportSolution`
action (`action: "ImportSolution"` in the import envelope): no progress ticks,
the request blocks until the import finishes, there is no async operation to
poll (`async_operation_id` is null), and a **declared** missing dependency
fails the import loudly (fault `0x80048033`). Two evidence holes remain:
per-component results may still be unavailable (the import's `meta.warnings`
says why), and an import whose components carry **undeclared** dangling
references can report success while leaving broken state — only read-back
catches it.

**Fallback verification — confirm components actually landed.** When
`import-result` has nothing, or a clean result needs corroborating, read the
target back:

```bash
# snapshot taken from the SOURCE org before export (see drift section below)
crm --json solution components MySolution --diff expected.json
crm --json metadata entity new_widget       # spot-check key components exist
```

## Check what an exported solution needs before importing (`missing-components`)

Run against the **import target** org before importing. An empty result means the
org already has everything the solution needs — go ahead with `solution import`.
A non-empty list names what must be installed first (or the import will fail with
a dependency fault).

```bash
crm --json solution missing-components ./MySolution.zip
# → {ok, data:[<missing components>], meta:{count}}
```

The argument is a **path to an exported `.zip`** (not a unique name — the API
requires the file bytes). Read-only; fires under `--dry-run` too.

**Gotcha — URL-length limit.** The bytes ride in the query string as a parameter
alias. Excessively large zips can hit the server's URL-length ceiling (inherent
to `RetrieveMissingComponents`, not the CLI). If you hit it, pre-validate
offline with `solution validate` and split the solution if it is genuinely too large.

## Preview what blocks uninstalling a managed solution

```bash
crm --json solution dependencies CRMWorx
```

The solution-scoped counterpart to `metadata dependencies` (see
`reference/metadata.md`): calls `RetrieveDependenciesForUninstall(SolutionUniqueName=
'<name>')` and returns `{solution, blockers[], count}`, each blocker shaped like the
metadata-dependency blockers. Read-only; the GET fires under `--dry-run`. Unknown
solution name → clean `{ok:false}`. Use this for "what stops me uninstalling solution
X?"; use `metadata dependencies` for a single component.

## Project a solution into a desired-state spec — `export-spec`

The **source side of the org-to-org drift recipe**: project every component in a solution
— entities, security roles, and web resources — into one merged apply-consumable spec,
then run `apply --dry-run` against the target org to see what drifts — pure reads, no
writes on either side.

```bash
# Dev org: write the bare YAML spec
crm solution export-spec MyCustomSolution -o desired.yaml

# Target org: preview schema drift, zero writes
crm --dry-run apply -f desired.yaml
```

Without `-o`, the JSON envelope carries a summary `data` payload plus the `skipped` bucket.

**JSON contract (without `-o`):** `data.entities` / `data.optionsets` are name lists,
`data.security_roles` / `data.webresources` are name lists, `data.attributes` is a total
count; there is no `meta`.
```json
{
  "ok": true,
  "data": {
    "solution": "MyCustomSolution",
    "entities": ["cwx_Ticket", "cwx_Project"],
    "attributes": 12,
    "optionsets": ["cwx_priority"],
    "security_roles": ["Contoso Project Manager"],
    "webresources": ["contoso_/scripts/project.js"],
    "skipped": [
      {"type": "pluginassembly", "objectid": "<guid>",
       "reason": "plug-in component not projectable from a live org (assembly DLL bytes absent); ..."}
    ]
  }
}
```
With `-o FILE`, `data` is instead `{path, solution, entities: <count>, attributes,
optionsets: <count>, security_roles: <count>, webresources: <count>, skipped}`.

**Security roles** project as `security_roles[]` — name, optional `business_unit`, and
privileges grouped by depth into `privilege_names` selector rows. Roles whose privileges
are all at non-authorable depths (e.g. RecordFilter) are routed to `skipped`.

**Web resources** project as `webresources[]` — body carried inline as base64 `content`
(no sidecar file), plus `display_name` and `webresourcetype`. The inline form requires
`webresourcetype` when applied (apply cannot infer the type without a file extension).

**Skipped bucket** — components that cannot be projected from live metadata (plug-in
assemblies, forms, dashboards, workflows, and other non-seedable types) land here. The
verb **never fails** on an unsupported component (exit 0, `ok: true`) and never drops one
silently.

**Known limitation:** projection is driven by entity members only — a subcomponent member
is NOT resolved to its parent entity. So attribute / view / relationship members **always**
appear in `skipped`; their data is still exported when the parent entity is itself a solution
member (projected in full), and only a lone subcomponent whose parent entity is absent is
genuinely not exported (ADR 0019).

The emitted spec includes a top-level `solution:` key so `apply --dry-run` auto-scopes its
drift/prune report. For a single entity, use `metadata export-spec` (see
`reference/metadata.md`); `solution export-spec` composes it across every entity in the
solution.

## Component drift detection — `components --save` / `--diff`

Snapshot and compare solution contents for CI gates or agent branching:

```bash
# Capture the expected inventory (normalized bare JSON list)
crm --json solution components CRMWorx --save components.json
# -> {"ok": true, "data": {"saved": "components.json", "count": 42}}

# Compare live against the snapshot — exit 0 = matches, exit 1 = drift
crm --json solution components CRMWorx --diff components.json
# on match:  {"ok": true,  "data": {"matches": true, "missing": [], "unexpected": []}, "meta": {"matches": true}}
# on drift:  {"ok": false, "data": {"matches": false, "missing": [...], "unexpected": [...]},
#             "error": "Drift detected: 1 missing, 0 unexpected component(s)."}
```

Snapshot/diff entries use the normalized 3-key form: `{"componenttype": <int>,
"objectid": "<guid-lowercase>", "rootcomponentbehavior": <int|null>}`. Components are
keyed on the tuple `(componenttype, objectid, rootcomponentbehavior)` — `missing` = in
expected not live; `unexpected` = in live not expected. **Exits 1 on drift.** The flags
are mutually exclusive; bare `components <name>` (no `--save`/`--diff`) emits the full
OData rows including `componenttypename` (the friendly string for the integer
`componenttype`, e.g. `"entity"`, `"webresource"`, `"sla"`) — so agent code does not
need to maintain its own type-code map.

## Unmanaged-layer conflicts — `layer-conflicts`

Find managed components that *also* carry unmanaged-layer customizations (the
classic source of upgrade surprises). The on-prem detection path that XrmToolBox's
Solution Layers Explorer can't give you — it needs the online-only
`msdyn_componentlayer`; this verb needs only `solutioncomponents`, so it runs
identically on v9.x on-prem and Dataverse online.

```bash
crm --json solution layer-conflicts --solution MyManagedSln --unmanaged-solution MyDevSln
# overlap:  {"ok": true, "data": [{"componenttype": 1, "type_name": "entity",
#            "objectid": "…", "managed_rootcomponentbehavior": 0,
#            "unmanaged_rootcomponentbehavior": 0}], "meta": {"count": 1}}
# none:     {"ok": true, "data": [], "meta": {"count": 0}}   (human: "no conflicts found")
```

The result is the **intersection** of the two solutions' components, keyed on
`(componenttype, objectid)` — `rootcomponentbehavior` is *ignored* for matching (the
row carries both sides' values for inspection). **Always exits 0** when the flags
resolve to the right kinds, conflicts or not — this is a report, not a gate (contrast
`components --diff`, which exits 1 on drift). Kind is validated client-side: a
wrong-kind flag (managed where unmanaged is expected, or vice versa) fails with
`{ok:false}`, exit 1, naming the offending flag — before any comparison.

**Granularity limit:** matching is per solution-component. A table added whole to the
managed solution whose single attribute was customized in the unmanaged solution shows
**no** conflict — the attribute is a separate component (own `objectid`/type).
Subcomponent correlation is out of scope.
