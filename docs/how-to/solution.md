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
Returns component **type + objectid** rows (componenttype `9` = option set, `1` = entity) — use it to verify the model landed.

## Add or remove a component

```bash
# add an existing web resource (componenttype 61) to an unmanaged solution
crm --json solution add-component --solution CRMWorx --type webresource --id <guid>

# remove it again (destructive — prompts unless --yes)
crm --json solution remove-component --solution CRMWorx --type 61 --id <guid> --yes
```
Wrap the `AddSolutionComponent` / `RemoveSolutionComponent` actions. `--type` takes a `componenttype` integer **or** a friendly name (`entity`, `attribute`, `relationship`, `optionset`, `webresource`, …; names are case- and separator-insensitive — `WebResource`, `web resource`, `web-resource` all resolve to `61`). Pass a raw integer for any type not in the name map. Both refuse a **managed** solution client-side (a managed solution can't be edited). Note the canonical split: `relationship` is `3` (base relationship) and `entityrelationship` is `10` — not interchangeable.

`add-component` is non-destructive. `AddRequiredComponents` defaults on (`--no-add-required` turns it off) and subcomponents are included by default (`--no-subcomponents` sets `DoNotIncludeSubcomponents: true`).

`remove-component` is **destructive**: it prompts for confirmation (aborting cleanly with `{"ok": false, "error": "aborted by user"}` in a non-TTY context) unless `--yes`, and the agent-side PreToolUse hook blocks it without `--yes` ([#71](https://github.com/Gharib89/crm/issues/71)).

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

## Import a solution zip

```bash
crm solution import docs/artifacts/crmworx.zip --yes
```
By default an import **overwrites unmanaged customizations** in the target org, so it is gated as a destructive operation: without `--yes` it prompts for confirmation and, in a non-TTY context, aborts cleanly (exit 1 — under `--json` the body is `{"ok": false, "error": "aborted by user"}`, otherwise a human-formatted error). Always pass `--yes` when invoking non-interactively (agents, CI). Add `--no-overwrite` to keep existing unmanaged customizations — that path skips the in-band prompt, but the [destructive-op gate](../reference/cli.md) still requires `--yes` for any import since it mutates the org ([#67](https://github.com/Gharib89/crm/issues/67)).

On completion the result parses the import job's `data` column into a solution-level `result` (`success`/`warning`/`failure`) plus a `components` list — `{name, type, result, errorcode?, errortext?}` per imported component. **A component that failed under an overall-succeeded job is no longer hidden:** any non-success component adds a `meta.warnings` note, so `status: succeeded` can't mask a partial failure ([#70](https://github.com/Gharib89/crm/issues/70)). Add `--formatted` to also attach the Excel-format `RetrieveFormattedImportJobResults` report verbatim under `formatted_results` (opt-in — it is a separate round-trip).

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
