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

## Export the unmanaged solution to a zip

```bash
crm solution export CRMWorx -o docs/artifacts/crmworx.zip
```
Reports the output path, byte count, `managed: False`, and the `action` that ran (falls back to synchronous `ExportSolution` when `ExportSolutionAsync` is disabled on-prem). (No `--json` here — this writes a binary zip, not a JSON result.)

## Publish all customizations

```bash
crm --json solution publish-all
```
Calls `PublishAllXml` so newly created metadata, views, forms, charts and dashboards surface in the app.
