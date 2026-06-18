# How-to: view

List and create system views (savedquery), taken from the CRMWorx build (§6, §13). See the
[CLI reference](../reference/cli.md) for every flag.

## List the public views for an entity

```bash
crm view list cwx_ticket
```

Output columns: `name`, `savedqueryid`, `isdefault`, `querytype`. Mirrors
`crm form list` — use it to find a view's `savedqueryid` before editing or
deleting it.

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

## Create a non-public view (`--query-type`)

By default `view create` makes a **public** view. Pass `--query-type` to create a
different [savedquery type](https://learn.microsoft.com/power-apps/developer/model-driven-apps/customize-entity-views#types-of-views):
`public`, `advanced-find`, `associated`, `quick-find`, or `lookup`. Choosing
`quick-find` additionally marks the view as the entity's quick-find query
(`isquickfindquery`), so it backs the global search box rather than appearing in
the grid view picker.

```bash
crm --json view create cwx_ticket --name "Quick Find Active Tickets" --otc 10127 \
  --column "cwx_name:220" --column "cwx_priority:120" \
  --query-type quick-find --if-exists skip
```

The existence check is per `name`+`returnedtypecode`+`querytype`, so the same
name can coexist across different query types (e.g. a public and a quick-find
view), and `--if-exists skip` only matches a prior view of the **same** type.

## Set a view description (`--description`)

`--description` writes the optional `savedquery.description`. Omit it to leave the
view with no description (the prior default).

```bash
crm --json view create cwx_sla --name "Active SLAs (cmd)" --otc 10126 \
  --column "cwx_name:240" --column "cwx_tier:140" --filter-active \
  --description "SLAs in the active state, sorted by name." --if-exists skip
```
