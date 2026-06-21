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

## Edit an existing view's columns (`edit-columns`)

`edit-columns` modifies the grid columns of an existing saved query in place —
no manual XML editing required. It keeps the layoutxml and fetchxml coupled: `--add`
writes both the layout cell and the fetch attribute; `--remove` drops both.

```bash
# Add a column (default width 100), remove another, resize a third
crm --json view edit-columns cwx_ticket "Active Tickets" \
  --add cwx_severity:120 \
  --remove cwx_legacy_field \
  --width cwx_priority:80

# Reorder columns (must be a complete permutation of the current set)
crm --json view edit-columns cwx_ticket "Active Tickets" \
  --reorder cwx_name,cwx_priority,cwx_severity,cwx_createdon
```

**Resolve by GUID when a name is ambiguous.** The view is resolved by
`name + returnedtypecode + querytype`. If more than one savedquery matches, the
command errors and tells you to pass the `savedqueryid` directly instead of a name.
Get the id from `crm view list <entity>`.

**`--reorder` is not combinable with `--add`/`--remove`/`--width`.** It takes a
comma-separated list that must be a permutation of the current column names — any
column missing from the list or any unknown column name is an error.

**Refused cases:** the primary-key column cannot be removed; a view whose
`IsCustomizable.Value` is false is refused with a clear error.

**Publish-then-read-back.** The command publishes by default (`--publish`). The
T3 read-back verification only runs when publish is enabled — under `--no-publish`
the response reflects the staged state, and a subsequent `view list` GET will still
return the *published* (pre-edit) columns until you publish.

**Managed-layer warning.** Editing an out-of-box or managed view creates an
unmanaged layer. A later solution upgrade may revert the change.

**Non-public views.** `--query-type` defaults to `public`. For quick-find, lookup,
or associated views, pass the matching type — and note that `view list` shows only
public views, so capture the `savedqueryid` from the response if you need it later.

## Set a view's sort order (`set-order`)

`set-order` rewrites the `<order>` elements in the view's fetchxml without
touching anything else (filters, conditions, link-entities are left intact).

```bash
# Replace the sort entirely
crm --json view set-order cwx_ticket "Active Tickets" \
  --order "cwx_priority asc" --order "createdon desc"

# Append to the current sort without replacing it
crm --json view set-order cwx_ticket "Active Tickets" \
  --add-order "modifiedon desc"

# Remove all sorting
crm --json view set-order cwx_ticket "Active Tickets" --clear-order
```

Each `--order` / `--add-order` value is `attribute` or `attribute asc|desc`
(bare attribute = ascending). The attribute is validated to exist on the entity
before any write.

`--order` and `--add-order` may be combined (replace first, then append to the
new sort). `--clear-order` removes all sorting and is normally used on its own.

The same **ambiguous-name**, **managed-layer**, and **publish-then-read-back**
notes from `edit-columns` apply here too.
