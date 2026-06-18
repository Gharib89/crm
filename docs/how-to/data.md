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
kept verbatim — numbers stay numbers, booleans stay booleans, and a hand-written
lookup bind (`"<nav>@odata.bind": "/<set>(<guid>)"`) is passed through unchanged.

### Round-tripping an export (READ-shape lookups auto-rebind)

`data import` rewrites any lookup that arrives in the server's **READ shape**
`_<attr>_value` (the raw-GUID form `data export` and `query odata` emit) into the
WRITE shape `"<nav>@odata.bind": "/<set>(<guid>)"`, resolving the navigation
property and target entity set from relationship metadata. So an exported row
imports unedited — no manual `@odata.bind` editing. (`entity create` and
`entity upsert` do the same on their `--data` payloads.) Specifics:

- A read-only lookup value (e.g. `_createdby_value`) is dropped — it can't be
  written. Read-only OData annotation keys (`@odata.etag`, `@odata.context`,
  formatted-value and per-value annotations) are stripped; a hand-written
  `<nav>@odata.bind` you provide is preserved.
- A `null` `_<attr>_value` clears the lookup (`<nav>@odata.bind: null`).
- A payload already in write shape (plain columns + your own `@odata.bind`, no
  `_value`/annotation keys) is left untouched — no metadata fetch.
- **Polymorphic lookups (`customerid`, `ownerid`, …) need annotations.** A
  Customer/Owner lookup binds to the concrete target named by its
  `@Microsoft.Dynamics.CRM.lookuplogicalname` annotation. When that annotation is
  **absent** the lookup is silently dropped (not an error) so the rest of the
  record still round-trips — matching `entity clone`'s never-copy-`ownerid`
  behavior. A plain `data export` carries no annotations and `ownerid` is on every
  record, so to round-trip a polymorphic lookup you must export **with**
  annotations.

This is lookup-only: non-lookup read-only / unique scalar fields are not stripped,
so a whole-record export may still be rejected on those (a separate concern), and
there is no export-side "import-ready" flag.

### Upsert records by GUID

```bash
crm data import contacts contacts_update.jsonl \
    --mode upsert --id-column contactid
```

`--mode upsert` issues a PATCH to `contacts(<guid>)` for each row. The column
named by `--id-column` is removed from the record body before the PATCH is sent.

### Upsert records by alternate key

When you do not have primary GUIDs in the file, upsert by a natural/alternate
key instead:

```bash
crm data import accounts accounts.jsonl \
    --mode upsert --key accountnumber
```

Each row is PATCHed to `accounts(accountnumber='<value>')`. The key column(s)
are read from the row and stripped from the request body before sending.
`--key` is mutually exclusive with `--id-column`; `--mode upsert` (and
`--mode delete`, below) requires one of them. `--key` and `--id-column` apply
only to `--mode upsert` or `--mode delete` — using either under `--mode create`
is a usage error.

Composite alternate keys are comma-separated:

```bash
crm data import cwx_slas slas.jsonl \
    --mode upsert --key cwx_tier,cwx_region
```

`--key` validates the named attribute(s) form a **defined** alternate key on the
entity before processing the first row — an unknown or unregistered combination
returns a clean error listing the defined keys. List alternate keys with
`crm metadata keys <entity>`.

### Delete records in bulk

```bash
# Delete by GUID
crm data import contacts to_delete.jsonl --mode delete --id-column contactid

# Delete by alternate key
crm data import accounts to_delete.jsonl --mode delete --key accountnumber
```

`--mode delete` issues a `$batch` DELETE per row, keyed by `--id-column` (the
record GUID) or `--key` (an alternate key) — resolved exactly as `--mode upsert`
resolves the target record (composite keys, validation, and body handling are
identical; DELETE carries no body). Like the other modes it reports per-row
success/failure in `data.failures` and respects the global `--dry-run` flag
(zero writes, `dry_run: true`). As with upsert, exactly one of `--id-column` or
`--key` is required.

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
warning line in human mode; exit code is 0 on partial failure. `--json` also
returns a per-record `data.failures` array and human mode prints one line per
failed record alongside the aggregate warning.

Each `data.failures` entry shape: `{index, id?, status, error}`, where `index` is
the 1-based input row and `id` is present for upsert and delete (the GUID or
alternate-key segment that addressed the row). A row that fails with
the alternate-key uniqueness violation code (`0x80060892`) additionally carries
best-effort `alternate_keys` (each `{name, schema_name, attributes, payload_values}`)
and, when the row's payload also contains the primary-id attribute, a
`primary_id_hint` string — the same hint `entity create --json` attaches for
single-record duplicate-key errors. The key schema is fetched once per import run
and `payload_values` is per row. These fields are absent when the schema lookup
fails or the row's error code is different.

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
