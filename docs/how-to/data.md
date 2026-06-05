# How-to: data

Bulk dataset import and export recipes, taken from the CRMWorx build (§4). See the
[CLI reference](../reference/cli.md) for every flag.

## Import

All imports are routed through the Dataverse `$batch` endpoint — the only
bulk-write mechanism available on-prem (`CreateMultiple`/`UpsertMultiple` are
cloud-only).

### Create records from a JSONL file

```bash
crm data import accounts records.jsonl
```

Format is inferred from the file suffix (`.jsonl` or anything non-`.csv` →
JSONL). Each line must be a JSON object; blank lines are skipped. Values are
kept verbatim — numbers stay numbers, booleans stay booleans, and lookup binds
(`"<nav>@odata.bind": "/<set>(<guid>)"`) are passed through unchanged.

### Upsert records by GUID

```bash
crm data import contacts contacts_update.jsonl \
    --mode upsert --id-column contactid
```

`--mode upsert` issues a PATCH to `contacts(<guid>)` for each row. The column
named by `--id-column` is removed from the record body before the PATCH is sent.

### Import from CSV

```bash
crm data import cwx_tickets tickets.csv
```

Format is inferred from the `.csv` suffix (override with `--format csv`). Cell
values are coerced best-effort: empty → null, `true`/`false` (case-insensitive)
→ bool, integer-looking strings → int, float-looking strings → float, everything
else → string.

**Caveat:** non-finite tokens (`NaN`, `inf`, `Infinity`) are kept as strings, and
integer-looking strings with leading zeros (`"007"`, postal codes) are coerced to
integers — losing the leading zeros. For IDs, postal codes, and lookup
`@odata.bind` values, prefer JSONL.

### Chunked non-transactional import with continue-on-error

```bash
crm data import accounts large_accounts.jsonl \
    --chunk-size 50 --no-transaction --continue-on-error
```

`--chunk-size` controls how many records go into each `$batch` call (default
100). By default each chunk is a transactional changeset (atomic — all-or-nothing
per chunk). `--no-transaction` sends each operation as a top-level batch
operation instead. `--continue-on-error` asks the server to continue past
individual failures (`Prefer: odata.continue-on-error`); it requires
`--no-transaction` because a changeset is itself all-or-nothing.

When `failed > 0`, a warning is surfaced in `meta.warnings` (`--json`) or as a
warning line in human mode; exit code is 0 on partial failure.

### Dry-run preview (zero writes)

```bash
crm --dry-run data import accounts records.jsonl
```

Use the global `--dry-run` flag to preview the import without issuing any writes.
The summary shows `imported: 0` and `dry_run: true`.

---

## Export

### Export a table to CSV

```bash
crm data export cwx_tickets -o docs/artifacts/crmworx-tickets.csv \
  --select cwx_name,cwx_priority,cwx_severity,cwx_category
```
Writes a CSV (default format) to `-o`; reports the output path, `format`, and row `count`.

### Export to JSON instead

```bash
crm data export cwx_tickets -o cwx_tickets.json --format json \
  --select cwx_name,cwx_priority,cwx_severity,cwx_category
```
`--format json` emits a JSON array; omit `--select` to export every column.

### Export a filtered, capped subset

```bash
crm data export cwx_tickets -o high_priority.csv \
  --filter "cwx_priority eq 3" --max-records 500 --page-size 100
```
`--filter` takes an OData `$filter`; `--page-size` controls the per-call page and `--max-records` caps the total rows written.
