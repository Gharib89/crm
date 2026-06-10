# Solutions — lifecycle, packager, validate, drift

Create, version, export/import solutions; bridge to SolutionPackager for source
control; validate zips offline; detect component drift. Group: `solution`.
Flags/choices: `crm solution --help`.

## Solution scaffolding — publisher + solution

```bash
crm --json solution create-publisher --name crmworx --display CRMWorx \
    --prefix cwx --option-value-prefix 30000 --if-exists skip

crm --json solution create --name CRMWorx --publisher crmworx --if-exists skip
```

With a named profile active, both verbs auto-wire `publisher_prefix` (from
`create-publisher`) and `default_solution` (from `create`) back into it, so later
`metadata create-*` commands target that prefix/solution by default. Pass
`--no-set-default` to opt out.

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
`relationship`=3, `optionset`=9, `entityrelationship`=10, `webresource`=61, …; raw int
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
crm solution export MyCustomSolution -o /tmp/snap.zip
# returns {"output": "/tmp/snap.zip", "bytes": 123456, "managed": false, ...}
```

## Import a solution

```bash
crm solution import /tmp/snap.zip --yes
# --no-overwrite prevents overwriting existing unmanaged customizations in the target
```

**Gotcha:** `solution import` **OVERWRITES** unmanaged customizations in the target org by
default. Pass `--no-overwrite` to skip overwriting; omitting `--yes` in a non-interactive
context aborts.

## Source control — SolutionPackager bridge

Put a solution under source control with the offline SolutionPackager bridge (no
connection/profile needed; resolves the exe via `--solutionpackager-path` →
`CRM_SOLUTIONPACKAGER` → PATH, else errors naming the `Microsoft.CrmSdk.CoreTools`
NuGet). **`git diff` on the extracted tree IS the solution diff:**

```bash
crm solution extract --zipfile /tmp/snap.zip --folder src/MyCustomSolution
# ...commit + review git diff, then rebuild a zip from the tree
crm solution pack --zipfile dist/built.zip --folder src/MyCustomSolution
# a non-zero SolutionPackager exit fails the command;
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
against `processstages` — the `CreateProcessStage` duplicate-key class), and
existence of referenced web resources and global option sets in the target org
(requires a connection/profile). Use before `solution import`.

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

Each component entry: `{"componenttype": <int>, "objectid": "<guid-lowercase>",
"rootcomponentbehavior": <int|null>}`. Components are keyed on the tuple
`(componenttype, objectid, rootcomponentbehavior)` — `missing` = in expected not live;
`unexpected` = in live not expected. **Exits 1 on drift.** The flags are mutually
exclusive; bare `components <name>` lists components unchanged.
