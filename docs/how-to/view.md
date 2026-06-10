# How-to: view

Create system views (savedquery), taken from the CRMWorx build (§6, §13). See the
[CLI reference](../reference/cli.md) for every flag.

## Create an active-records public view

```bash
crm --json view create cwx_sla --name "Active SLAs (cmd)" --otc 10126 \
  --column "cwx_name:240" --column "cwx_tier:140" --filter-active --if-exists skip
```
Get `<otc>` (ObjectTypeCode) from `crm --json metadata entity cwx_sla`. `--filter-active` restricts to `statecode=0`; `--if-exists skip` makes re-runs a no-op. Generates the LayoutXml + FetchXml, creates, and publishes.

## Create a sorted view with several columns

```bash
crm --json view create cwx_ticket --name "Tickets by Priority" --otc 10127 \
  --column "cwx_priority:120" --column "cwx_name:220" --column "cwx_severity:120" \
  --order cwx_priority --if-exists skip
```
`--column` is repeatable as `logicalname[:width]` (order preserved); `--order` sets the sort attribute. Use column **logical names** (e.g. `cwx_priority`), not the option-set names. Get the `--otc` value the same way: `crm --json metadata entity cwx_ticket`.

## Sort newest-first (descending)

`--order` takes an optional `asc`/`desc` suffix — the same `$orderby` idiom as `query odata --orderby`. Bare attribute = ascending.

```bash
crm --json view create cwx_ticket --name "Recent Tickets" --otc 10127 \
  --column "cwx_name:220" --column "createdon:140" \
  --order "createdon desc" --if-exists skip
```
This writes `descending="true"` into the view's FetchXml at create time — no follow-up `entity update savedqueries` PATCH. An invalid direction token (anything but `asc`/`desc`) is a usage error (exit 2).
