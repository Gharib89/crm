# How-to: solution

Solution lifecycle recipes, taken from the CRMWorx build (§1, §5). See the
[CLI reference](../reference/cli.md) for every flag.

## Create a publisher, then the solution (zero web-UI prerequisite)

```bash
crm --json solution create-publisher --name crmworx --display CRMWorx --prefix cwx \
  --option-value-prefix 30000 --if-exists skip
crm --json solution create --name CRMWorx --publisher crmworx --if-exists skip
```
Create both from the CLI before any metadata work ([#34](https://github.com/Gharib89/crm/issues/34)); with a named profile active they auto-wire `publisher_prefix=cwx` and `default_solution=CRMWorx`. `--if-exists skip` makes re-runs a no-op.

## List the components in a solution

```bash
crm --json solution components CRMWorx
```
Returns one row per component with **`componenttype`, `objectid`, and `rootcomponentbehavior`** (componenttype `9` = option set, `1` = entity) — use it to verify the model landed. Those three fields are the tuple key used by `--save`/`--diff` below.

## Detect drift: save & diff a component inventory

```bash
# Capture the expected inventory once (normalized bare JSON list)
crm --json solution components CRMWorx --save components.json

# Later: compare live components against the saved snapshot
crm --json solution components CRMWorx --diff components.json
```

`--save` writes a normalized JSON list to `<path>` (parent dirs created as needed) and emits `{"saved": "<path>", "count": N}`. Each entry carries exactly three keys: `{"componenttype": <int>, "objectid": "<guid-lowercase>", "rootcomponentbehavior": <int|null>}`.

`--diff` fetches live components and compares them against the file, keying each component on the tuple `(componenttype, objectid, rootcomponentbehavior)`. The `data` field contains `{"matches": bool, "missing": [...], "unexpected": [...]}` — `missing` = in expected but not live, `unexpected` = in live but not expected. **Exits non-zero (1) on drift** so agents and CI can branch on `$?`; exit 0 means the live solution matches the snapshot exactly.

The two flags are mutually exclusive; bare `components <name>` is unchanged. The round-trip `--save` then `--diff` against the same org reports no drift ([#82](https://github.com/Gharib89/crm/issues/82)).

## Detect unmanaged-layer conflicts across two solutions

```bash
crm --json solution layer-conflicts --solution MyManagedSln --unmanaged-solution MyDevSln
```

Reports components present in **both** a managed and an unmanaged solution — i.e. managed components that also carry unmanaged-layer customizations, the potential unmanaged-layer conflicts. The result is the **intersection** of the two solutions' `solutioncomponents`, keyed on `(componenttype, objectid)` and deliberately ignoring `rootcomponentbehavior` (the same component included with a different behavior is still an overlap). Each row carries `componenttype`, the friendly `type_name` (or the raw int as a string for an unmapped type), `objectid`, and both sides' `managed_rootcomponentbehavior` / `unmanaged_rootcomponentbehavior`.

Works identically on v9.x on-prem and Dataverse online — it needs only `solutioncomponents` (present since CRM 2011), not the online-only `msdyn_componentlayer`, so on-prem gets a detection path it otherwise lacks. Read-only: `--solution` must resolve to a **managed** solution and `--unmanaged-solution` to an **unmanaged** one (validated client-side; a wrong-kind flag fails with `{ok:false}` and exit 1 naming the offending flag). **Always exits 0** when both kinds are valid — conflicts found or not (reporting, not failure, unlike `components --diff`); zero conflicts emits an explicit "no conflicts found" message and an empty list with `meta.count = 0`.

**Limitation:** matching is at solution-component granularity. A table added whole to the managed solution whose single *attribute* was customized and added to the unmanaged solution intersects on nothing — the attribute is its own component with its own `objectid`/type. Subcomponent-level correlation is out of scope ([#200](https://github.com/Gharib89/crm/issues/200)).

## Add or remove a component

```bash
# add an existing web resource (componenttype 61) to an unmanaged solution
crm --json solution add-component --solution CRMWorx --type webresource --id <guid>

# remove it again (destructive — prompts unless --yes)
crm --json solution remove-component --solution CRMWorx --type 61 --id <guid> --yes
```
Wrap the `AddSolutionComponent` / `RemoveSolutionComponent` actions. `--type` takes a `componenttype` integer **or** a friendly name (`entity`, `attribute`, `relationship`, `optionset`, `webresource`, …; names are case- and separator-insensitive — `WebResource`, `web resource`, `web-resource` all resolve to `61`). Pass a raw integer for any type not in the name map. Both refuse a **managed** solution client-side (a managed solution can't be edited). Note the canonical split: `relationship` is `3` (base relationship) and `entityrelationship` is `10` — not interchangeable.

`add-component` is non-destructive. `AddRequiredComponents` defaults on (`--no-add-required` turns it off) and subcomponents are included by default (`--no-subcomponents` sets `DoNotIncludeSubcomponents: true`). Adding an **entity** with `AddRequiredComponents` on emits an informational `meta.note`: the server may silently add required components beyond the requested entity, and the response does not report them ([#181](https://github.com/Gharib89/crm/issues/181)).

`remove-component` is **destructive**: it prompts for confirmation (aborting cleanly with `{"ok": false, "error": "aborted by user"}` in a non-TTY context) unless `--yes`, and the agent-side PreToolUse hook blocks it without `--yes` ([#71](https://github.com/Gharib89/crm/issues/71)).

## Preview what blocks uninstalling a managed solution

```bash
crm --json solution dependencies CRMWorx
```
Calls `RetrieveDependenciesForUninstall` and returns the components that would block uninstalling that managed solution: `{solution, blockers[], count}`, each blocker carrying `dependent_type`, `dependent_id`, `dependent_parent_id`, `required_type`, `dependency_type` (the same shape as [`metadata dependencies`](metadata.md)). Human mode prints a blocker table; an empty result means nothing blocks the uninstall. Read-only — the GET fires even under `--dry-run`. This is the **solution-scoped** counterpart to `metadata dependencies`: that command targets a single component (entity/attribute/optionset/relationship); this one takes only a solution unique name. An unknown solution name returns a clean `{ok:false}` envelope ([#116](https://github.com/Gharib89/crm/issues/116)).

## Bump the version (or friendly name / description) before export

```bash
crm --json solution set-version CRMWorx --version 1.0.1.0
crm --json solution set-version CRMWorx --friendly-name "CRM Worx" --description "RC build"
```
Updates an **unmanaged** solution in place over the shared record-update path (so `--dry-run` previews the PATCH). `--version` must be 4-part dotted numeric and is validated before any HTTP; at least one field is required. Managed solutions and patches are rejected client-side (the server returns `CannotUpdateSolutionPatch` for a patch) ([#66](https://github.com/Gharib89/crm/issues/66)).

## Export the unmanaged solution to a zip

```bash
crm solution export CRMWorx -o docs/artifacts/crmworx.zip
```
Reports the output path, byte count, `managed: False`, and the `action` that ran (falls back to synchronous `ExportSolution` when `ExportSolutionAsync` is disabled on-prem). On success the zip is written to `-o/--output`; adding `--json` only changes the printed result envelope.

## Source-control a solution (extract / pack)

```bash
# Unpack an exported zip into a diff-able folder tree
crm solution extract --zipfile docs/artifacts/crmworx.zip --folder src/CRMWorx

# ...commit the tree, review `git diff`, then build a zip back from it
crm solution pack --zipfile dist/crmworx.zip --folder src/CRMWorx
```

`extract` / `pack` are thin wrappers over the CoreTools `SolutionPackager.exe`: `extract` unpacks an exported solution zip into a folder of XML/source files you can commit, and `pack` rebuilds an importable zip from that folder. There is no XML-diff engine — **`git diff` on the extracted tree _is_ the solution diff.**

These are **offline local-file transforms**: they never open a connection, and no profile or credentials are required. `--package-type` selects `Unmanaged` (default), `Managed`, or `Both`. The executable is resolved in order: `--solutionpackager-path` → the `CRM_SOLUTIONPACKAGER` environment variable → `PATH`. crm does **not** bundle or download SolutionPackager — install it from the [`Microsoft.CrmSdk.CoreTools`](https://www.nuget.org/packages/Microsoft.CrmSdk.CoreTools) NuGet package; an absent binary fails with an error naming it.

`--timeout` bounds the subprocess (seconds). The result envelope carries `{action, exit_code, folder, zipfile, stdout_tail}` (only the tail of SolutionPackager's chatty output is kept); a non-zero `exit_code` fails the command (`ok: false`, exit 1) while still reporting `stdout_tail` for diagnosis ([#73](https://github.com/Gharib89/crm/issues/73)).

## Validate a solution package before import

Catch packaging problems offline in one pass instead of one-error-per-import round-trip:

```bash
crm solution validate ./MySolution.zip
```

Offline checks: every component in `customizations.xml` is declared in
`solution.xml` `<RootComponents>` and vice-versa; `$webresource:` references in
ribbon XML resolve to a web resource in the package; every global option-set
binding is declared; both manifests are well-formed and all required members
(`solution.xml`, `customizations.xml`, `[Content_Types].xml`) are present.

Add `--against-org` to also check the connected org for colliding `formid` /
`savedqueryid` GUIDs, colliding BPF process-stage GUIDs (`StageId` /
`NextStageId` read from `Workflows/*.xaml` and probed against `processstages` —
the `CreateProcessStage` duplicate-key import failure), the existence of
referenced web resources and global option sets, and whether the package's
`SolutionPackageVersion` exceeds the target org version — a package newer than
the org (even a newer minor) fails import with `0x80048068` ("you can only import
solutions with a package version of {org} or earlier"). The version check is
best-effort: an absent/unparseable package version or an org version that can't
be read degrades to a warning/skip and never falsely flips the report invalid.
Requires a connection/profile:

```bash
crm solution validate ./MySolution.zip --against-org
```

`validate` exits non-zero when any error-severity problem is found, so it drops
straight into a pre-import CI gate.

## Import a solution zip

```bash
crm solution import docs/artifacts/crmworx.zip --yes
```
By default an import **overwrites unmanaged customizations** in the target org and **activates imported workflows** (`PublishWorkflows`, not `PublishAllXml`), so it is gated as a destructive operation: without `--yes` it prompts for confirmation and, in a non-TTY context, aborts cleanly (exit 1 — under `--json` the body is `{"ok": false, "error": "aborted by user"}`, otherwise a human-formatted error). Always pass `--yes` when invoking non-interactively (agents, CI). Use `--no-overwrite` to keep existing unmanaged customizations, or `--no-publish` to suppress workflow activation — both are the off-halves of boolean pairs (`--overwrite/--no-overwrite`, `--publish/--no-publish`); the positive spellings are also accepted. The `--no-overwrite` path skips the in-band overwrite prompt, but the [destructive-op gate](../reference/cli.md) still requires `--yes` for any import since it mutates the org ([#67](https://github.com/Gharib89/crm/issues/67)).

On completion the result parses the import job's `data` column into a solution-level `result` (`success`/`warning`/`failure`) plus a `components` list — `{name, type, result, errorcode?, errortext?}` per imported component. **A component that failed under an overall-succeeded job is no longer hidden:** any non-success component adds a `meta.warnings` note, so `status: succeeded` can't mask a partial failure ([#70](https://github.com/Gharib89/crm/issues/70)). Add `--formatted` to also attach the Excel-format `RetrieveFormattedImportJobResults` report verbatim under `formatted_results` (opt-in — it is a separate round-trip).

The result also includes a `managed` field: `true` if the imported solution is managed, `false` if unmanaged, or `null` when the flag could not be read (e.g. a corrupt zip). This is sniffed from `solution.xml` inside the zip before the upload and is present in dry-run results too ([#91](https://github.com/Gharib89/crm/issues/91)).

On on-prem orgs that reject `ImportJobId` on `ImportSolutionAsync` (v9.x), the command transparently retries with the synchronous `ImportSolution` action carrying the same id (`action: "ImportSolution"` in the result), so `import_job_id` is always non-null and `import-result` works there too ([#182](https://github.com/Gharib89/crm/issues/182)). On that path the whole import runs inside one HTTP request — the read timeout follows `--timeout` (default: the profile's `async_timeout`), and no progress ticks are emitted. A missing-dependency import fails loudly as a synchronous error (naming the `import_job_id`) instead of reporting a bare `status: succeeded`; if the platform still provides no per-component results after a successful import, `meta.warnings` says so explicitly.

If an import is blocked by a **product-update dependency** (the server rejects it before processing components), add `--skip-dependency-check` to set `SkipProductUpdateDependencies: true` in the request body and allow the import to proceed past that check ([#376](https://github.com/Gharib89/crm/issues/376)). This flag applies to both the async and the synchronous-fallback path.

## Verify a prior import

```bash
crm --json solution import-result <import_job_id>
```
Re-fetches a completed import job by id and runs the same parser, returning the per-component pass/fail envelope (and the same `meta.warnings` on any non-success component) without re-importing. The `<import_job_id>` is the `import_job_id` reported by `solution import`. Add `--formatted` for the Excel-format report ([#70](https://github.com/Gharib89/crm/issues/70)).

## Upgrade a managed solution (clone-as-patch, stage-and-upgrade, apply-upgrade, uninstall)

First-class verbs for the managed-solution lifecycle, so an agent never has to drop to the raw `CloneAsPatch`/`DeleteAndPromote` server actions. They all work on both v9.x on-prem and Dataverse online, and compose with `--dry-run` and `--json`.

**Clone a parent solution to a patch** (`CloneAsPatch`):

```bash
crm --json solution clone-as-patch --solution CRMWorx
```
Creates a patch solution and returns `{cloned, parent_solution, display_name, version, patch_solutionid}`. When `--version` is omitted the parent's version is read and its **revision** (4th part) is bumped — e.g. a parent at `1.0.0.0` yields patch `1.0.0.1`; a patch must keep the parent's `major.minor`. `--display` defaults to the parent's friendly name. A missing parent fails before any POST.

**Stage an upgrade as a holding solution** (`ImportSolution` `HoldingSolution`):

```bash
crm solution stage-and-upgrade docs/artifacts/crmworx_2_0.zip --yes
```
Imports the zip in *holding* mode — staged for upgrade, not yet applied — reusing the same import pipeline as `solution import` (per-component result parsing, the on-prem synchronous `ImportSolution` fallback, progress ticks). Gated as destructive: `--yes` skips the prompt for non-interactive use.

**Apply the staged upgrade** with `--promote` (`DeleteAndPromote` — replaces the base solution and its patches with the holding solution):

```bash
crm solution stage-and-upgrade docs/artifacts/crmworx_2_0.zip --promote --solution CRMWorx --yes
```
`--promote` requires `--solution` (the unique name to promote, exit 2 otherwise). It runs only after a real, succeeded stage — never under `--dry-run` — and attaches the promote result under `data.promote`.

**Apply a separately-staged upgrade** (`DeleteAndPromote` — the standalone promote path):

```bash
crm solution apply-upgrade CRMWorx --yes
```
Promotes a solution already staged via `stage-and-upgrade` (run **without** `--promote`), decoupling stage-time from promote-time — the same `DeleteAndPromote` the one-shot `stage-and-upgrade --promote` runs, replacing the base solution and deleting its patches. Gated as destructive (`--yes`).

**Uninstall a solution** (`DELETE /solutions(<id>)`):

```bash
crm solution uninstall --solution CRMWorx --yes
```
Resolves the solutionid, then **pre-checks `RetrieveDependenciesForUninstall`** and refuses with the blocker count unless `--force` (use [`solution dependencies`](#preview-what-blocks-uninstalling-a-managed-solution) to inspect the blockers first). For a managed base solution the server also uninstalls its patches. Gated as destructive (`--yes`).

## Publish all customizations

```bash
crm --json solution publish-all
```
Calls `PublishAllXml` so newly created metadata, views, forms, charts and dashboards surface in the app.
