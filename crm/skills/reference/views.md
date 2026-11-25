# Views — create and edit system views (savedquery)

Create a view, then edit its columns, sort order and FetchXML filter. Group:
`view`. Flags/choices: `crm view --help`. Declaring views, tables and columns in an
`apply` spec: `reference/authoring.md`.

## Views — `view create` (savedquery)

```bash
crm --json view create contoso_ticket --name "Active Tickets" --otc 10127 \
    --column "contoso_name:220" --column "contoso_priority:120" \
    --filter-active --solution ContosoCore --if-exists skip
```

The LayoutXml `object` attribute is the entity **ObjectTypeCode (OTC)** — get it from
`metadata entity <name>` (see `reference/metadata.md`). `--column` is repeatable
`'logical[:width]'` with order preserved.

`--order-by` takes an optional `asc`/`desc` suffix (same `$orderby` idiom as
`query odata --order-by`): `--order-by createdon` is ascending, `--order-by 'createdon desc'`
sorts newest-first by writing `descending="true"` into the FetchXml at create time —
no follow-up savedquery PATCH. Bad direction token → usage error (exit 2).

`--query-type` (see `--help` for the choices) selects the savedquery type; the
default is a public grid view. Two non-obvious effects: picking the quick-find
type also flips `isquickfindquery` on the row (so the view backs global search,
not the grid picker), and the existence guard keys on name+entity+**type** — the
same name can coexist across types, and `--if-exists skip` only matches a prior
view of the same type. **Gotcha:** `view list` shows only public views, so a
non-public view you create this way will not appear there — capture its
`savedqueryid` from the `view create` output if you need to edit it later.

### Edit an existing view's columns — `view edit-columns`

```bash
crm --json view edit-columns account "All Accounts" \
    --add telephone1:120 --remove fax --width name:200 --solution ContosoCore
crm --json view edit-columns account "All Accounts" \
    --reorder name,telephone1,emailaddress1 --solution ContosoCore
```

Every `view` editor verb (`edit-columns`, `set-order`, `add-filter`,
`remove-filter`) is solution-scoped.

**Mismatch invariant.** `--add` writes both the layoutxml `<cell>` and the fetchxml
`<attribute>` in one PATCH — a cell without a matching attribute leaves a column with
no data, so the CLI always keeps them coupled. Likewise `--remove` drops both. The
primary-key cell+attribute are protected and cannot be removed.

**Ambiguous name → resolve by GUID.** The savedquery table has no alternate key.
`edit-columns` resolves by `name + returnedtypecode + querytype`; if more than one
row matches, the command errors. Run `crm --json view list <entity>` to get the
`savedqueryid`, then pass that GUID as the `<view>` argument.

**Non-public views.** Pass `--query-type` (advanced-find, associated, quick-find,
lookup) to target a non-public view. `view list` shows only public views.

**Publish-then-read-back.** With `--publish` the verb also GETs the view back to
confirm the edit landed; without it the read-back is skipped and a subsequent GET
returns the *published* (pre-edit) snapshot until you publish. `layoutjson` is
cleared on every column edit so the platform rebuilds it from the new layoutxml (a
stale layoutjson drives the modern grid with the old columns).

**Managed-layer warning** (see `--help`): editing an out-of-box or managed view
layers over it unmanaged — a solution upgrade may revert the edit.

### Set a view's sort order — `view set-order`

```bash
crm --json view set-order account "All Accounts" \
    --order "name asc" --order "createdon desc" --solution ContosoCore
crm --json view set-order account "All Accounts" --add-order "modifiedon desc" --solution ContosoCore
crm --json view set-order account "All Accounts" --clear-order --solution ContosoCore
```

Only the entity's direct `<order>` children are touched — `<filter>`, `<condition>`,
and `<link-entity>` elements are left intact. Order attributes are validated against
live metadata before any write. Same ambiguous-name, managed-layer, and
publish-then-read-back notes as `edit-columns`.

### Add FetchXML filter conditions — `view add-filter`

```bash
crm --json view add-filter contoso_ticket "Active Tickets" \
    --condition "statecode eq 0" --solution ContosoCore
crm --json view add-filter contoso_ticket "Active Tickets" \
    --condition "contoso_priority in 1 2 3" --condition "contoso_severity ne 3" --solution ContosoCore
crm --json view add-filter contoso_ticket "Active Tickets" \
    --condition "contoso_resolvedon null" --solution ContosoCore
```

Conditions are appended to the entity-level `<filter>` (created if absent).
`<link-entity>` filters and existing conditions are never touched. The condition
attribute is validated against live metadata before any write.

**Operator cardinality** — the non-obvious part: no-value operators (`null`,
`not-null`, `today`, `eq-userid`, …) take no value tokens;
`between`/`not-between` take exactly two; `in`/`not-in`/`contain-values`/
`not-contain-values` take a list (emitted as child `<value>` elements);
all other operators take a single value (remaining tokens are joined, so
`name eq Contoso Ltd` works). Wrong cardinality is rejected before any write.

Same ambiguous-name, managed-layer, and publish-then-read-back notes as
`edit-columns`.

### Remove FetchXML filter conditions — `view remove-filter`

```bash
crm --json view remove-filter contoso_ticket "Active Tickets" \
    --condition "statecode eq 0" --solution ContosoCore
# disambiguate when attribute+operator match multiple conditions:
crm --json view remove-filter contoso_ticket "Active Tickets" \
    --condition "contoso_priority in 1 2 3" --solution ContosoCore
```

Matched on attribute + operator; supply values to disambiguate. No match or
multiple matches → error. The attribute need not still exist on the entity (so
filters on deleted columns can be cleaned up). An empty `<filter>` after
removal is pruned. Link-entity filters are never searched.

Same ambiguous-name, managed-layer, and publish-then-read-back notes as
`edit-columns`.
