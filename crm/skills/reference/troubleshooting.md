# Troubleshooting — errors, retries, connection diagnostics, session

Classify failures, decide what is safe to retry, diagnose a broken connection, and
inspect local session state. Flags/choices: `crm connection --help`,
`crm profile --help`, `crm session --help`.

## Error taxonomy & recovery

`D365Error` wraps any HTTP / API failure. In `--json` mode it becomes
`{"ok": false, "error": "...", "meta": {"status": N, "code": "0x...", "category": "...", "retryable": bool}}`.
A non-transactional optionset update that fails mid-stage adds `meta.completed_steps` +
`meta.failed_stage` so the partial mutation is observable; all other failures carry
only the four keys above.

`meta.category` is a closed enum; `meta.retryable` flags the transient classes.

| `category` | trigger | `retryable` | recovery |
|---|---|---|---|
| `not_found` | 404 / code `0x80040217` | no | record doesn't exist, or wrong entity set / GUID |
| `auth_failed` | 401 | no | NTLM: check the profile's domain / username + password (re-run `crm profile set-password`). OAuth: app-registration (client id/secret, tenant) + an application user with a role |
| `forbidden` | 403 | no | the user lacks the privilege; for `security assign-role` this also fires when the role's business unit differs from the target's (roles are BU-scoped — assign one from the same BU) |
| `concurrency_conflict` | 412 | yes | another change won the race — retrieve a fresh ETag and retry |
| `duplicate_detected` | code `0x80040237` | no | a matching record exists; merge/resolve or pass `--suppress-dup-detection` |
| `validation` | other 4xx (e.g. 400), or a status-less client-side error (bad CLI input, schema/spec validation) | no | fix the request: bad payload / CLI input, alternate key, or OData syntax |
| `throttled` | 429 | yes | service-protection limit; the backend honors `Retry-After` (idempotent verbs only) |
| `server_error` | 5xx | yes | transient server fault (idempotent verbs only) |
| `transport_error` | request never got a response; message starts `HTTP transport failure` | yes | network / TLS / timeout before any response (idempotent verbs only) |

Note `status is None` is **not** a reliable transport-failure signal — client-side
validation errors also have no status. Only the `transport_error` message prefix marks
the genuine no-response path.

The bare OData `in` predicate is unsupported (Web API is OData 4.0) — use
`Microsoft.Dynamics.CRM.In(PropertyName='...',PropertyValues=[...])` or `query fetchxml`.

Investigating a **failed solution import** (`job-status`, `import-result`, the on-prem
evidence hole, fallback verification): see `reference/solutions.md`.

## Retry semantics

The backend **auto-retries** the `transport_error` / `throttled` (429) / `server_error`
(5xx) classes for **idempotent verbs** (`GET`/`PUT`/`PATCH`/`DELETE`), so for those
`retryable: true` is a post-exhaustion hint — act on it only after the error surfaces.
Two cases never auto-retry:

- `concurrency_conflict` (412) — refetch a fresh ETag and retry.
- Any non-idempotent `POST` (record create, action, associate) — a lost response may
  mean the write **already landed**, so the backend surfaces the error rather than risk
  a duplicate side effect. To retry a create safely, re-run it as an upsert-by-id
  (`entity upsert` with a client-supplied GUID) so the second call is idempotent; or, if
  re-sending is acceptable, pass `--retry-on-ambiguous` (env: `CRM_RETRY_ON_AMBIGUOUS`)
  to restore POST auto-retry.

`$batch` keeps its own independent retry loop and is unaffected by this gate.

## Connection diagnostics

When a command can't reach the server or auth is failing, run the live diagnostic
before guessing:

```bash
crm --json connection doctor          # or the top-level alias: crm doctor
crm --json connection whoami          # confirm the live target (check @odata.context host)
crm --json connection status          # active profile + resolved config
```

`doctor` exercises the full auth + request path; for OAuth profiles a failure may
surface as a `D365Error` raised **during** the request (token acquisition), not as a
plain network error — read the `meta.category` to tell auth from transport.

## On-prem vs cloud reminders

The same commands hit both targets; only auth + API version differ. On-prem caps at
API **v9.1** (`v9.2` → HTTP 501), and `CreateMultiple`/`UpdateMultiple`/`DeleteMultiple`
are **cloud-only** — which is why bulk `data import` routes through `$batch` (see
`reference/records.md`). The active profile selects the target; `crm profile list`
shows it and `crm --json connection whoami` confirms the live host.

## Session & audit

```bash
crm --json session info       # local session state
crm --json session history    # recent commands
crm --json session audit      # journal of mutations issued from this CLI
crm session clear             # reset local session state
```

The audit journal is local bookkeeping of the mutations this CLI issued — useful for
reconstructing what an agent run changed. It is not the server-side D365 audit log.

## Server-side audit history

To read the D365 server's own audit records use `crm audit`, not `crm session audit`.

```bash
crm --json audit history <entity-set> <record-guid>   # paged AuditDetailCollection
crm --json audit detail  <auditid-guid>               # single decoded AuditDetail
```

**Prerequisites:** the calling user needs `prvReadRecordAuditHistory` + `prvReadAuditSummary`
privileges, and auditing must be enabled on the org/table/columns — otherwise the server
returns an empty `AuditDetailCollection` (no error). Each `AuditDetail` entry exposes
`AuditDetailType` (e.g. `AttributeAuditDetail`) — the Web API `@odata.type` discriminator
promoted to a plain field because the standard emit envelope strips all `@odata.*` keys.

These functions (`RetrieveRecordChangeHistory`, `RetrieveAuditDetails`) cannot be called
via `action function`: it now emits parameter aliases for record-reference params
(`{"@odata.id": "set(guid)"}`) and reserved-char values, but not the arbitrary
complex-type arguments (e.g. a `PagingInfo` object) these functions require.
