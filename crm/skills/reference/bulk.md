# Bulk data — export, import, server-side delete, raw `$batch`

Move records in volume: CSV/JSONL export and import over `$batch`, server-side
BulkDelete jobs, and hand-authored `$batch` files. Groups: `data`, top-level
`batch`. Flags/choices: `crm data --help`, `crm batch --help`. Single-record
CRUD and queries: `reference/records.md`.

## Bulk CSV export

```bash
crm data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue,closeprobability \
    --page-size 500
```

## Bulk import via `$batch`

All writes are routed through `$batch` — the only on-prem bulk mechanism
(`CreateMultiple`/`UpsertMultiple` are cloud-only).

```bash
# Create records from a JSONL file (format inferred from suffix)
crm data import accounts records.jsonl

# Upsert (PATCH by GUID); id-column is stripped from the record body
crm data import contacts contacts.jsonl --mode upsert --id-column contactid

# Upsert by alternate key; key column(s) stripped from each row's body
crm data import accounts accounts.jsonl --mode upsert --key accountnumber

# Composite alternate key
crm data import contoso_slas slas.jsonl --mode upsert --key contoso_tier,contoso_region

# CSV import (best-effort coercion; prefer JSONL for IDs / postal codes / lookups)
crm data import contoso_tickets tickets.csv

# Non-transactional + continue-on-error (requires --no-transaction)
crm data import accounts large.jsonl \
    --chunk-size 50 --no-transaction --continue-on-error

# Dry-run preview — zero writes, summary shows imported:0 dry_run:true
crm --json --dry-run data import accounts records.jsonl
```

**`--mode upsert` and `--mode delete` each require exactly one of `--id-column`
or `--key`; passing both is a usage error. `--key`/`--id-column` are rejected
under `--mode create`.** `--mode delete` resolves the target record by GUID or
alternate key exactly as upsert does (DELETE carries no body) and reports per-row
results in `data.failures` just like the other modes. Only `--mode delete` uses
the destructive confirmation path; create/upsert imports do not prompt.

**Alternate-key import gotcha.** `--key` resolves and validates the key against
entity metadata before the first row is processed. A not-yet-`Active` key index
404s **per row** — each row fails individually in `data.failures`, not as one
bulk failure. Poll `metadata keys` until the index is `Active` first (the
index-status note in `reference/metadata.md`).

Output: `{imported, failed, chunks, entity_set, mode, dry_run, format, failures}`.
`failures` is **always present** (empty `[]` when nothing failed); each entry is
`{index, id?, status, error}` — `index` is the 1-based input-row position, `id` the
record GUID or alternate-key segment (present under `--mode upsert`/`--mode delete`,
not `create`), `status` the server HTTP status, `error` the
server message. `failed > 0` also surfaces a `meta.warnings` count advisory; **exit code
is 0 on partial failure** — read `data.failures` for which rows failed and why (no need
to re-issue rows to discover it), don't rely on `$?`. (This is a different per-row shape
from `entity clone`'s `{entity, source_id, reason}` in `reference/records.md`, and
here `ok` stays `true`.)

Scrub and validate the input **before** a big run (`--dry-run`, a small pilot
chunk) rather than discovering bad rows via thousands of `failures` entries. On
Dataverse online, sustained bulk traffic also hits service-protection throttling
(HTTP 429) — retried automatically (`reference/troubleshooting.md`), but it
stretches wall-clock time; keep big loads sequential, not parallel.

**Alternate-key collision hint.** A row that fails with the alternate-key uniqueness
code (`0x80060892`) additionally carries best-effort enrichment fields on its
`failures` entry:

```json
{
  "index": 3,
  "status": 412,
  "error": "A record with matching key values already exists.",
  "alternate_keys": [
    {
      "name": "accountnumber_key",
      "schema_name": "accountnumber_key",
      "attributes": ["accountnumber"],
      "payload_values": {"accountnumber": "ACC-001"}
    }
  ]
}
```

`alternate_keys` lists every defined alternate key on the entity (each
`{name, schema_name, attributes, payload_values}`), where `payload_values` is the
intersection of the key's attribute names with the failing row's payload. When the
row's payload also contains the entity's primary-id attribute, a `primary_id_hint`
string is added to warn that the server returns the same error code for a
primary-key collision too. The schema is fetched at most once per import run
(identical for every row). Both fields are absent when the schema lookup fails or
the row's code is different — enrichment is strictly best-effort and never masks
the original error.

## Server-side BulkDelete — `data delete`

`crm data delete` submits a **server-side D365 BulkDelete async job**. D365 runs the
deletion inside the server — no records are pulled to the client. This is distinct from
`data import --mode delete`, which issues one HTTP DELETE per row via `$batch`.

**Why FetchXML.** The Web API `BulkDelete` action's `QuerySet` accepts only a
`QueryExpression`. There is no server-side OData→QueryExpression path, so the CLI takes
FetchXML and converts it via `FetchXmlToQueryExpression` before submitting. Passing an
OData `$filter` directly is not possible.

**JSON contract — submit (no `--wait`):**
```json
{"job_id": "<guid>", "job_name": "crm data delete contacts", "status": "submitted", "match_count": 42}
```

**JSON contract — with `--wait`:**
```json
{"job_id": "<guid>", "job_name": "...", "match_count": 42, "status": "completed", "succeeded": 42, "failed": 0}
```

**JSON contract — under `--dry-run`:**
```json
{"_dry_run": true, "would_submit": "BulkDelete", "entity_set": "contacts", "job_name": "...", "match_count": 42}
```

**`--yes` is required for non-interactive use** (SKILL.md destructive contract).

**`--dry-run` is safe.** FetchXML is validated and the matched count is reported; no job
is submitted.

**Gotcha — `match_count` is a snapshot.** It reflects the live row count when the job was
submitted. The async job may encounter more or fewer rows as it runs (concurrent writes).

## Raw `$batch` — `crm batch`

`crm batch <file.json>` runs a hand-authored `$batch` directly — the escape hatch for
mixed/cross-entity bulk work that `data import` (single-entity) can't express, e.g.
deleting many records in one round-trip. The file is a **JSON array of operation
objects**, each carrying a `method` and `url` (plus a `body` on writes):

- `method` — `GET` | `POST` | `PATCH` | `DELETE`.
- `url` — a **bare relative path** (`contacts(<guid>)`, `accounts`), no leading slash.
- `body` — JSON object; **required** on `POST`/`PATCH`, **rejected** on `GET`/`DELETE`.
- optional `headers` (object of string values) and `content_id` (str/int, to reference a
  just-created record from a later op in the same changeset via `$<content_id>`).

**Gotcha — `url` must not begin with `/`.** A leading slash resolves against the host
root, not the Web API path, and 404s. `crm batch` blocks it client-side before any
request with a `validation` error telling you the `url` must be a bare relative path —
fix the file, don't retry.

Minimal bulk delete — two contacts, atomic (see grouping note below):

```bash
cat > bulk-delete.json <<'EOF'
[
  {"method": "DELETE", "url": "contacts(00000000-0000-0000-0000-000000000001)"},
  {"method": "DELETE", "url": "contacts(00000000-0000-0000-0000-000000000002)"}
]
EOF
crm --json batch bulk-delete.json
# -> data: [{...,"status":204},{...,"status":204}], meta: {total, success, failed}
```

Transaction grouping (default mode): each run of **consecutive writes**
(`POST`/`PATCH`/`DELETE`) is wrapped in one atomic changeset (all-or-nothing rollback),
while every `GET` stays a top-level op and breaks the run — so a file that interleaves
reads and writes produces *several* changesets, not one. An all-write file like the
bulk-delete above is therefore a single atomic unit. `--no-transaction` drops the
changesets and sends every op top-level; `--continue-on-error` (which requires
`--no-transaction`) keeps going past a failed op. **Exit code is 0 even when some ops
fail** — read each result's `status` and `meta.failed`, don't rely on `$?`.
