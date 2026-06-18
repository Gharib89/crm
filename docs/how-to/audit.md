# How-to: audit

Server-side audit history from Dynamics 365 — distinct from the local
`session audit` journal, which records only this CLI's own mutations. See the
[CLI reference](../reference/cli.md) for every flag.

## Prerequisites

- The calling user needs the **`prvReadRecordAuditHistory`** and
  **`prvReadAuditSummary`** privileges.
- Auditing must be enabled at the org level, the table level, and (for column-level
  detail) the column level in D365. When auditing is off the server returns a
  well-formed but empty `AuditDetailCollection` — no error, just zero rows.

## Retrieve a record's change history

```bash
crm --json audit history accounts <record-guid>
```

Returns an `AuditDetailCollection` with the paging fields `MoreRecords` (bool),
`PagingCookie` (str), and `TotalRecordCount` (int), plus an `AuditDetails` array
— one entry per audited change event.

Each `AuditDetail` entry carries an `AuditDetailType` field (e.g.
`AttributeAuditDetail`) derived from the Web API `@odata.type` discriminator.
The standard emit envelope strips all `@odata.*` keys (ADR 0008), so without
this promotion the subtype would be invisible — `AuditDetailType` is the
canonical field to branch on.

## Page through a long history

`MoreRecords: true` means more pages exist. Pass the returned `PagingCookie`
to fetch the next page:

```bash
# Page 1 (default)
crm --json audit history accounts <record-guid> --count 20

# Page 2 — copy the PagingCookie from the page 1 response
crm --json audit history accounts <record-guid> --count 20 \
    --paging-cookie '<cookie from prior response>'
```

The `--page` option selects a 1-based page number when you know the target
page upfront (no cookie needed). Combine `--page` with `--count` to jump
directly to a page of a known size.

## Retrieve a single audit row by ID

```bash
crm --json audit detail <auditid-guid>
```

Decodes the `AuditDetail` for one row in the `audits` table. The concrete type
is surfaced as `AuditDetailType` (same promotion as above). Use this to inspect
the full before/after field values for a specific change event when you already
have the `auditid` from an `audit history` result.

## Why not `action function`?

`RetrieveRecordChangeHistory` requires a `Target` EntityReference and a
`PagingInfo` complex object passed as OData parameter aliases
(`?@target=...&@paginginfo=...`). The `action function` command encodes
parameters inline as `Fn(k=v)`, which cannot express complex-type aliases.
The `audit` group handles this encoding internally, making these functions
reachable from the CLI for the first time.

## Session audit vs server-side audit

| | `crm session audit` | `crm audit history` |
|---|---|---|
| What it records | Mutations issued by **this CLI** in the current session | All audited changes stored in the **D365 server** audit log |
| Where data lives | Local JSONL file (`~/.crm/audit/<session>.jsonl`) | D365 `audits` entity, server-side |
| Connection needed | No | Yes |
| Privilege needed | None (local file) | `prvReadRecordAuditHistory` + `prvReadAuditSummary` |
