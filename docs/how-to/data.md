# How-to: data

Bulk dataset export recipes, taken from the CRMWorx build (§4). See the
[CLI reference](../reference/cli.md) for every flag.

## Export a table to CSV

```bash
crm data export cwx_tickets -o docs/artifacts/crmworx-tickets.csv \
  --select cwx_name,cwx_priority,cwx_severity,cwx_category
```
Writes a CSV (default format) to `-o`; reports the output path, `format`, and row `count`.

## Export to JSON instead

```bash
crm data export cwx_tickets -o cwx_tickets.json --format json \
  --select cwx_name,cwx_priority,cwx_severity,cwx_category
```
`--format json` emits a JSON array; omit `--select` to export every column.

## Export a filtered, capped subset

```bash
crm data export cwx_tickets -o high_priority.csv \
  --filter "cwx_priority eq 3" --max-records 500 --page-size 100
```
`--filter` takes an OData `$filter`; `--page-size` controls the per-call page and `--max-records` caps the total rows written.
