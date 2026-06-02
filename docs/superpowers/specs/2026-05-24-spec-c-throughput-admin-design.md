# Spec C — Throughput + admin surface

**Date:** 2026-05-24
**Status:** Approved (pending user review of written spec)
**Target version:** 0.4.0
**Tracking issue:** [#5](https://github.com/Gharib89/crm/issues/5)
**Predecessor:** [Spec B — Resilience](./2026-05-24-spec-b-resilience-design.md) (shipped as 0.3.0)

---

## 1. Goals + non-goals

### Goals

- `$batch` endpoint support via backend helper `D365Backend.batch(operations)` and `crm batch <file.json>` CLI. JSON-listed ops; consecutive writes auto-grouped into one changeset (transactional); GETs go as top-level operations. `--no-transaction` opts out of changeset grouping.
- Impersonation via `MSCRMCallerID`. Per-command `--as-user <systemuserid-guid>` flag and `CRM_AS_USER` env. Applied to every write verb in the affected invocation.
- `MSCRM.SuppressDuplicateDetection` header. Per-command `--suppress-dup-detection` and `CRM_SUPPRESS_DUP`. Applies to POST/PATCH/Upsert.
- `MSCRM.BypassCustomPluginExecution` header. Per-command `--bypass-plugins` and `CRM_BYPASS_PLUGINS`. Caller must hold `prvBypassCustomPluginExecution`; server rejects otherwise.
- `asyncoperations` browse: `crm async list/get/cancel` plus `crm solution job-status/job-cancel` aliases.
- Optimistic concurrency: `backend.patch(url, body, etag=...)` and `backend.delete(url, etag=...)` send `If-Match`. CLI: `entity update --if-match <etag>` and `entity delete --if-match <etag>`. 412 surfaces as `D365Error(status=412, code="PreconditionFailed")`.
- Bump to **0.4.0**. All additions are additive flags/helpers.

### Non-goals

- `CreateMultiple` / `UpdateMultiple` / `UpsertMultiple`: Dataverse cloud messages (rolled out ~2022); not in Contoso 9.1.x on-prem Web API per the v9.0 changelog. Deferred to a future spec contingent on target-server upgrade.
- `CallerObjectId` impersonation header: requires Microsoft Entra ID object identifier; on-prem AD users do not have one. Out of scope.
- Auto-read-then-write ETag wrapping: explicit `--if-match` only this spec; transparent optimistic-locking is a future feature.
- Spec D (metadata write API) and Spec E (DX polish) unchanged.

### Breaking changes

None. All additions are additive flags + helpers + new commands. No existing CLI command or return shape changes.

---

## 2. Architecture

No new modules in `crm/utils/`. Two new files in `crm/core/`.

```
crm/
  utils/
    d365_backend.py        — typed admin-header kwargs on request/get/post/patch/delete,
                             etag= kwarg, batch() helper, multipart codec
    d365_types.py          — BatchOperation, BatchResult, AsyncOperationRow TypedDicts
  core/
    async_ops.py           — NEW: list_async_operations, get_async_operation, cancel_async_operation
    batch.py               — NEW: parse_batch_file, render_batch_summary
  cli.py                   — `batch`, `async` command group; --as-user / --suppress-dup-detection
                             / --bypass-plugins / --if-match flags on write verbs
setup.py                   — bump to 0.4.0
CHANGELOG.md               — 0.4.0 section
```

`async_ops.py` and `batch.py` are in the pyright strict zone (Spec A §2 `crm/core/*` rule). `d365_backend.py` stays strict. `cli.py` stays basic.

### 2.1 Header-injection point

`D365Backend.request` already accepts `extra_headers: dict[str, str] | None`. Add typed kwargs so callers do not stringify header names and values at call sites:

```python
def request(
    self,
    method: str,
    path: str,
    *,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    expect_json: bool = True,
    caller_id: str | None = None,
    suppress_duplicate_detection: bool = False,
    bypass_custom_plugin_execution: bool = False,
    etag: str | None = None,
) -> dict[str, Any] | str | None:
```

`get` / `post` / `patch` / `delete` forward the same kwargs. The four typed kwargs win over any colliding entry in `extra_headers` (deterministic precedence; documented). Env defaults (`CRM_AS_USER`, `CRM_SUPPRESS_DUP`, `CRM_BYPASS_PLUGINS`) are resolved once at `D365Backend.__init__` into `self._default_caller_id`, `self._default_suppress_dup`, `self._default_bypass_plugins`; per-call kwargs always override.

### 2.2 `etag=` semantics

- `etag=None` (default) → no `If-Match` header.
- `etag="*"` → `If-Match: *` (matches any current version).
- `etag='W/"123"'` → `If-Match: W/"123"` verbatim.
- Empty string → `D365Error("etag must be non-empty")`.
- GET/POST + `etag` → `D365Error("etag not valid on <method>")` raised before HTTP.

---

## 3. Admin headers

### 3.1 ConnectionProfile additions

None. Admin headers are per-invocation by design. No profile fields, no `to_dict` / `from_dict` impact.

### 3.2 Env resolution

Resolved once at `D365Backend.__init__`:

| Env var               | Backend default attr                | Type                                            |
|-----------------------|-------------------------------------|-------------------------------------------------|
| `CRM_AS_USER`         | `_default_caller_id`                | str (GUID; validated via `uuid.UUID(value)`)    |
| `CRM_SUPPRESS_DUP`    | `_default_suppress_dup`             | bool (`0`/`1`/`true`/`false`, case-insensitive) |
| `CRM_BYPASS_PLUGINS`  | `_default_bypass_plugins`           | bool                                            |

Parse errors raise `D365Error` at backend construction (matches the Spec B pattern for retry env vars).

### 3.3 Per-call header assembly

Inside `D365Backend.request`, after merging `extra_headers` onto the base headers and before issuing the HTTP call:

```python
headers = dict(self._base_headers)
if extra_headers:
    headers.update(extra_headers)

effective_caller = caller_id or self._default_caller_id
if effective_caller is not None:
    try:
        uuid.UUID(effective_caller)
    except ValueError as exc:
        raise D365Error(f"Invalid GUID for caller_id: {effective_caller!r}") from exc
    headers["MSCRMCallerID"] = effective_caller

if suppress_duplicate_detection or self._default_suppress_dup:
    headers["MSCRM.SuppressDuplicateDetection"] = "true"

if bypass_custom_plugin_execution or self._default_bypass_plugins:
    headers["MSCRM.BypassCustomPluginExecution"] = "true"

if etag is not None:
    if not etag:
        raise D365Error("etag must be non-empty")
    if method.upper() not in ("PATCH", "DELETE"):
        raise D365Error(f"etag not valid on {method}")
    headers["If-Match"] = etag
```

Header keys are stable string literals — no dynamic construction. Validation runs before any HTTP I/O so retry logic is never entered on a bad input.

### 3.4 CLI flags on write verbs

Affected commands in `cli.py` (every existing write/action verb):

- `entity create`
- `entity update`
- `entity delete`
- `entity upsert`
- `entity associate`
- `entity disassociate`
- `entity set-lookup`
- `entity clear-lookup`
- `workflow activate`
- `workflow deactivate`
- `workflow run`

Each gains:

| Flag                        | Default     | Effect                                       |
|-----------------------------|-------------|----------------------------------------------|
| `--as-user <guid>`          | env or none | `MSCRMCallerID: <guid>`                      |
| `--suppress-dup-detection`  | env or off  | `MSCRM.SuppressDuplicateDetection: true`     |
| `--bypass-plugins`          | env or off  | `MSCRM.BypassCustomPluginExecution: true`    |

Read-only commands (`entity get`, `query *`, `metadata *`, `connection *`, `solution list/info/components`, `session *`) do not get these flags — the server ignores them on reads, and exposing dead options confuses users.

`solution` write/action commands (`import`, `export`, `publish`, `publish-all`) are intentionally out of scope for this spec — they already carry a focused flag set; admin headers on solution actions can be added in a follow-up if a user demand surfaces.

`entity update` and `entity delete` additionally gain `--if-match <etag>`. The documented escape hatch is `--if-match "*"`.

### 3.5 Error mapping

- Server `403` with `prvBypassCustomPluginExecution` in the response body → re-raise as `D365Error(status=403, code="MissingPrivilege")` with a hint that the caller lacks `prvBypassCustomPluginExecution`.
- Server `412` on `If-Match` mismatch → `D365Error(status=412, code="PreconditionFailed")` with the row's current etag if the response body's `error.innererror` carries it.
- All other 4xx/5xx flow through the existing `_parse_response` path unchanged.

---

## 4. `$batch` endpoint

### 4.1 Backend helper signature

```python
def batch(
    self,
    operations: Sequence[BatchOperation],
    *,
    transactional: bool = True,
    continue_on_error: bool = False,
    timeout: int | None = None,
) -> list[BatchResult]:
    """Execute a list of operations via POST $batch.

    transactional=True (default): all consecutive writes are grouped into one
        changeset; GETs are top-level operations. Failure inside the changeset
        rolls back the changeset.
    transactional=False: every operation is top-level; no atomicity.
    continue_on_error=True: sends `Prefer: odata.continue-on-error`. Only
        meaningful with transactional=False.

    Returns one BatchResult per input operation, aligned to input order.
    """
```

`timeout=None` uses the backend's configured request timeout.

### 4.2 TypedDicts (`crm/utils/d365_types.py`)

```python
class BatchOperation(TypedDict, total=False):
    method: str            # "GET" | "POST" | "PATCH" | "DELETE"
    url: str               # relative to /api/data/v9.x/  (e.g., "accounts" or "accounts(<guid>)")
    body: dict[str, Any]   # optional; required-shape on POST/PATCH/UPSERT
    headers: dict[str, str]  # optional per-op headers (e.g., If-Match, MSCRMCallerID)
    content_id: str        # optional; for $<n> back-references inside a changeset

class BatchResult(TypedDict):
    method: str
    url: str
    status: int
    headers: dict[str, str]
    body: dict[str, Any] | str | None   # parsed JSON, raw text, or empty
    error: str | None
```

### 4.3 Multipart-MIME assembly

OData v4 `$batch` uses `multipart/mixed`. Layout for a mixed batch:

```
--batch_<guid>
Content-Type: application/http
Content-Transfer-Encoding: binary

GET accounts?$select=name HTTP/1.1
Accept: application/json

--batch_<guid>
Content-Type: multipart/mixed; boundary=changeset_<guid>

--changeset_<guid>
Content-Type: application/http
Content-Transfer-Encoding: binary
Content-ID: 1

POST accounts HTTP/1.1
Content-Type: application/json

{"name":"a"}

--changeset_<guid>--
--batch_<guid>--
```

Boundary IDs are `uuid.uuid4().hex`. The body is hand-assembled as a string with `\r\n` line endings and passed via `data=` to `requests.post`. `email.mime.multipart` is too lossy for the embedded HTTP line shape, so it is not used. Each sub-request URL is the relative path; the body's HTTP request-line uses `<METHOD> <relative-path> HTTP/1.1`.

### 4.4 Response parsing

Response is `multipart/mixed`. Parser uses `email.parser.BytesParser(policy=email.policy.HTTP)` to split parts. For each part:

- If `Content-Type` is `application/http`, parse the embedded HTTP status line + headers + body, populate `BatchResult`.
- If `Content-Type` starts with `multipart/mixed` (a changeset response), recurse one level into the sub-parts.

Non-2xx sub-status populates `error` with the response body's error message if JSON, otherwise the raw text. Body is still parsed (and stored in `body`) regardless of status so callers can read the full error envelope.

### 4.5 Result alignment

Each input operation is tagged with its zero-based input index. Top-level GET parts arrive in input order, so they consume the next GET index. Changeset parts use the `Content-ID: N` header to align with the write index they came from. The final `list[BatchResult]` is returned in input order.

### 4.6 Implicit changeset grouping

`batch()` walks `operations` once:

- A GET op emits one top-level part.
- A run of one-or-more consecutive write ops (POST/PATCH/DELETE) emits a single changeset part.
- `transactional=False` flattens everything: each op is its own top-level part, no changeset wrapping.

### 4.7 Size + count limits

Server-side caps (typical Dataverse defaults: 100 changesets per batch; 1000 operations per changeset) are not pre-validated client-side. The server returns `MaxBatchSize` / `MaxChangesetSize` errors when limits are exceeded; the user sees these verbatim. CHANGELOG documents the limits.

### 4.8 `crm batch` CLI

```
crm batch <file.json>
    [--no-transaction]
    [--continue-on-error]
    [--output <path>]
    [--timeout N]
```

JSON file shape: a list of `BatchOperation` dicts. Validation at load time:

- Each op has `method` and `url`.
- `method` is one of `GET`, `POST`, `PATCH`, `DELETE` (case-insensitive; normalized to upper).
- `body` only on POST/PATCH; rejected (with a clear error) on GET/DELETE.
- `content_id` (if present) is a non-empty string.

`--output <path>`: write `BatchResult[]` JSON to file. Default: stdout via `ctx.emit(ok=True, data=results)`.

`--continue-on-error` sets the `Prefer: odata.continue-on-error` header; only meaningful with `--no-transaction`. When passed with the default transactional mode, the CLI rejects with a usage error before any HTTP call.

Dry-run: when `backend.dry_run=True`, `crm batch` prints the assembled multipart body to stdout and exits 0 without sending — same pattern as other write commands.

### 4.9 Retry interaction (Spec B)

`POST $batch` is conservatively retryable per `_is_response_retryable` (POST + status 429 or 503 only). A retried batch re-sends the assembled body verbatim; idempotency is the user's responsibility. CHANGELOG documents this; the conservative POST policy from Spec B means non-rate-limit transient failures on `$batch` do not auto-retry.

---

## 5. `asyncoperations` browse

### 5.1 Backend helpers (`crm/core/async_ops.py`)

```python
def list_async_operations(
    backend: D365Backend,
    *,
    state: int | None = None,           # 0=Ready, 1=Suspended, 2=Locked, 3=Completed
    message_name: str | None = None,    # e.g., "ImportSolution"
    owner_id: str | None = None,        # systemuserid GUID
    top: int = 50,
    order_by: str = "createdon desc",
) -> list[AsyncOperationRow]: ...

def get_async_operation(
    backend: D365Backend,
    async_operation_id: str,
) -> AsyncOperationRow: ...

def cancel_async_operation(
    backend: D365Backend,
    async_operation_id: str,
) -> None:
    """PATCH asyncoperations(<id>) with statecode=3, statuscode=32 (Cancelled).
    Only succeeds for state in {0=Ready, 1=Suspended}; server returns 400 otherwise,
    which surfaces as D365Error unchanged."""
```

`AsyncOperationRow` (TypedDict in `d365_types.py`) covers `asyncoperationid`, `name`, `messagename`, `statecode`, `statuscode`, `createdon`, `startedon`, `completedon`, `_ownerid_value`, `errorcode`, `message`, `friendlymessage`.

`list_async_operations` builds an OData `$filter` from non-None kwargs and `$select`s the fields above only.

### 5.2 CLI commands

```
crm async list [--state ready|suspended|locked|completed|<int>]
               [--message <name>] [--owner <guid>]
               [--top N] [--all]
crm async get <id>
crm async cancel <id>
```

`--all` repeats `list` calls until exhausted (uses `@odata.nextLink`); without `--all`, `--top` defaults to 50.

`--state` accepts the four named values or an integer.

### 5.3 Solution aliases

```
crm solution job-status <async_operation_id>     → crm async get <id>
crm solution job-cancel <async_operation_id>     → crm async cancel <id>
```

Thin wrappers in `cli.py`; no extra logic. Documented as aliases in their help text.

### 5.4 Output

`crm async list` emits a table by default and JSON via the existing `-o json` global flag. Table columns: `id`, `message`, `state`, `status`, `created`, `owner`.

---

## 6. Optimistic concurrency (`If-Match`)

Header injection and validation: §2.2 + §3.3. CLI flag: §3.4. Error mapping: §3.5.

### 6.1 etag retrieval

`entity get` already returns `@odata.etag` when the row is fetched. The CLI help for `--if-match` documents that users copy that value verbatim.

### 6.2 Shell-quoting note

D365 etags are weak: shape `W/"<n>"`. Click strips outer shell quotes but not inner; users pass `--if-match 'W/"123"'` on POSIX shells; PowerShell users escape with backticks. The `--if-match` help string includes a short example for each shell.

---

## 7. Testing

### 7.1 Unit tests — `crm/tests/test_admin_headers.py` (new)

Pure-Python, no live server.

- Header assembly: each typed kwarg singly + combinations + env defaults overridden by per-call kwargs.
- GUID validation rejects garbage `caller_id`.
- `etag` rejects empty string; rejects GET/POST; accepts `"*"` and weak-etag formats.
- 412 maps to `D365Error(status=412, code="PreconditionFailed")`.
- 403/`prvBypassCustomPluginExecution` maps to `D365Error(status=403, code="MissingPrivilege")`.

### 7.2 Unit tests — `crm/tests/test_batch.py` (new)

- Multipart assembly: golden-file comparison with deterministic boundaries (`monkeypatch.setattr("uuid.uuid4", ...)`). Cases: all-GET; all-write (single changeset); mixed.
- Response parser: feed canonical multipart response bodies; assert `BatchResult` order matches input order; assert `error` populated on non-2xx sub-statuses; assert nested changeset response parsing.
- `transactional=False`: every op top-level; no changeset wrapper.
- `crm batch` CLI: JSON validation (missing `method`, invalid `method`, `body` on GET, `--continue-on-error` rejected with transactional default).

### 7.3 Unit tests — `crm/tests/test_async_ops.py` (new)

- `list_async_operations` builds correct `$filter` for each kwarg combination (single + multiple).
- `cancel_async_operation` issues PATCH with body `{"statecode": 3, "statuscode": 32}`.
- `crm async list --state ready` resolves to `statecode=0` filter; `--state 3` passes through.
- Solution aliases route to the same backend calls as `crm async get/cancel`.

### 7.4 E2E note

No live tests added in this spec. A smoke-test entry is added to `crm/tests/TEST.md` for running `crm batch sample.json` and `crm async list` against the Contoso 9.1.44.15 test box.

### 7.5 Pyright

All new code lands inside the strict zone. CI fails on any new strict error per the existing `.github/workflows/build.yml` pyright step from Spec A.

---

## 8. PR sequencing

| PR  | Branch                  | Contents                                                                                                                                                                                                                                                                                                                                                | Risk   |
|-----|-------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|
| **PR1** | `feat/spec-c-backend` | §2 typed kwargs on `request` / `get` / `post` / `patch` / `delete`; §3 admin-header injection + env resolution + GUID validation; §3.5 + §6 error mapping (412, 403/MissingPrivilege); §4 `batch()` helper + multipart codec + new TypedDicts in `d365_types.py`; §5.1 `async_ops.py` helpers; all unit tests (§7.1, §7.2 helper-level, §7.3). No CLI changes. No version bump. | Medium |
| **PR2** | `feat/spec-c-cli`     | §3.4 admin-header flags on every write verb; §3.4 `--if-match` flag on `entity update` / `entity delete`; §4.8 `crm batch` command + JSON validation; §5.2 `crm async list/get/cancel` + §5.3 solution aliases; §7.2 CLI-level batch tests. Bump `setup.py` to **0.4.0**. New `CHANGELOG.md` 0.4.0 section.                                                | Medium |

Merge order: PR1 → PR2. PR2 rebases on PR1.

---

## 9. Out of scope (deferred)

- **`CreateMultiple` / `UpdateMultiple` / `UpsertMultiple`** — Dataverse cloud messages (rolled out ~2022); Contoso 9.1.x lacks the SDK message. Revisit after the target server upgrades.
- **`CallerObjectId` impersonation** — requires Microsoft Entra ID object identifier; not applicable to on-prem AD users. `MSCRMCallerID` (this spec) is the on-prem-correct header.
- **Auto-read-then-write ETag wrapper** — explicit `--if-match` only this spec. Transparent optimistic locking is a future feature.
- **Spec D** — Metadata write API: add-attribute, create-relationship (1:N + N:N), global option set CRUD, delete-entity.
- **Spec E** — DX polish: `--verbose` HTTP transcript, structured logs, env-template generator, Kerberos / SSPI, REPL metadata-cache + tab completion, split `cli.py` per command group, `RetrieveTotalRecordCount`, `metadata list-actions` / `list-functions`.
