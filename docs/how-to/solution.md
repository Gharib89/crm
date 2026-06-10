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
the `CreateProcessStage` duplicate-key import failure), and for the existence of
referenced web resources and global option sets (requires a connection/profile):

```bash
crm solution validate ./MySolution.zip --against-org
```

`validate` exits non-zero when any error-severity problem is found, so it drops
straight into a pre-import CI gate.

## Import a solution zip

```bash
crm solution import docs/artifacts/crmworx.zip --yes
```
By default an import **overwrites unmanaged customizations** in the target org, so it is gated as a destructive operation: without `--yes` it prompts for confirmation and, in a non-TTY context, aborts cleanly (exit 1 — under `--json` the body is `{"ok": false, "error": "aborted by user"}`, otherwise a human-formatted error). Always pass `--yes` when invoking non-interactively (agents, CI). Add `--no-overwrite` to keep existing unmanaged customizations — that path skips the in-band prompt, but the [destructive-op gate](../reference/cli.md) still requires `--yes` for any import since it mutates the org ([#67](https://github.com/Gharib89/crm/issues/67)).

On completion the result parses the import job's `data` column into a solution-level `result` (`success`/`warning`/`failure`) plus a `components` list — `{name, type, result, errorcode?, errortext?}` per imported component. **A component that failed under an overall-succeeded job is no longer hidden:** any non-success component adds a `meta.warnings` note, so `status: succeeded` can't mask a partial failure ([#70](https://github.com/Gharib89/crm/issues/70)). Add `--formatted` to also attach the Excel-format `RetrieveFormattedImportJobResults` report verbatim under `formatted_results` (opt-in — it is a separate round-trip).

The result also includes a `managed` field: `true` if the imported solution is managed, `false` if unmanaged, or `null` when the flag could not be read (e.g. a corrupt zip). This is sniffed from `solution.xml` inside the zip before the upload and is present in dry-run results too ([#91](https://github.com/Gharib89/crm/issues/91)).

## Verify a prior import

```bash
crm --json solution import-result <import_job_id>
```
Re-fetches a completed import job by id and runs the same parser, returning the per-component pass/fail envelope (and the same `meta.warnings` on any non-success component) without re-importing. The `<import_job_id>` is the `import_job_id` reported by `solution import`. Add `--formatted` for the Excel-format report ([#70](https://github.com/Gharib89/crm/issues/70)).

## Publish all customizations

```bash
crm --json solution publish-all
```
Calls `PublishAllXml` so newly created metadata, views, forms, charts and dashboards surface in the app.
