# Spec B — Resilience Layer

**Date:** 2026-05-24
**Status:** Approved (self-review pass, user delegated final approval)
**Target version:** 0.3.0
**Tracking issue:** [#4](https://github.com/Gharib89/crm/issues/4)
**Predecessor:** [Spec A — Correctness + Pyright](./2026-05-24-spec-a-correctness-pyright-design.md) (shipped as 0.2.0)

---

## 1. Goals + non-goals

### Goals

- Add a transparent retry loop to `D365Backend.request` that honors `Retry-After`, applies capped exponential backoff with full jitter, and retries idempotent 5xx transport failures.
- Replace the synchronous `ImportSolution` and `ExportSolution` action calls with their `*Async` counterparts plus blocking client-side polling.
- Surface `x-ms-ratelimit-*` response headers when retry is triggered, and unconditionally in verbose mode.
- Expose retry + polling tuning via `ConnectionProfile` fields, `CRM_*` env overrides, and a CLI escape hatch.
- Bump to **0.3.0** to signal the `import_solution` / `export_solution` return-shape change.

### Non-goals

- No `$batch`, `CreateMultiple`, `UpdateMultiple`, `UpsertMultiple` — Spec C.
- No impersonation, `MSCRM.SuppressDuplicateDetection`, `MSCRM.BypassCustomPluginExecution`, `If-Match` ETag, `asyncoperations` browse — Spec C.
- No metadata write API — Spec D.
- No `--verbose` HTTP transcript, env-template generator, Kerberos / SSPI — Spec E.
- No StageSolution / staged-upgrade flow. `ImportSolutionAsync` accepts `CustomizationFile` directly (the same payload the current sync path uses) — staging is an additive feature, deferred.
- No async path for any action other than `ImportSolution` / `ExportSolution`. `poll_async_operation` is a reusable helper, but no other caller adopts it in this spec.

### Breaking changes

- `crm.core.solution.import_solution` return dict gains `async_operation_id`, `import_job_id`, `status`, `progress`, `started_on`, `completed_on`, `duration_ms`. Loses any field that came from the sync `ImportSolution` response that callers may have been reading. CHANGELOG documents the new keys.
- `crm.core.solution.export_solution` return dict gains `async_operation_id`, `export_job_id`, `duration_ms`. The `output`, `bytes`, `managed`, `solution` keys are preserved.
- Either function can now block for up to `CRM_ASYNC_TIMEOUT` seconds (default 1800). The sync versions blocked for up to `profile.timeout` seconds per HTTP call (default 120) with no client-side polling.

---

## 2. Architecture

No new modules. All edits land in two files:

```
crm/
  utils/d365_backend.py     — retry loop, poll_async_operation, profile fields
  core/solution.py          — import_solution + export_solution rewrites
setup.py                    — bump to 0.3.0
CHANGELOG.md                — new 0.3.0 section
```

### Module-graph rationale

`D365Backend.request` is the single HTTP chokepoint. Retry belongs there so every verb (`get` / `post` / `patch` / `delete`) inherits it without per-call wiring. `poll_async_operation` sits next to it because polling is just a sequence of `GET asyncoperations(<id>)` calls that themselves need to share the retry loop — promoting polling into the backend keeps the dependency arrow pointing in one direction (`core/*` → `utils/d365_backend`, never the reverse).

The `crm/utils/d365_backend.py` module is already in the pyright strict zone (per Spec A §2). New code stays strict-typed.

---

## 3. Retry mechanism

### 3.1 ConnectionProfile additions

```python
@dataclass
class ConnectionProfile:
    # ...existing fields...
    retry_max: int = 5
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    retry_jitter: bool = True
    async_poll_initial: float = 2.0
    async_poll_max: float = 30.0
    async_timeout: int = 1800
```

`to_dict` / `from_dict` round-trip the new fields with the defaults above. Existing profile JSON files on disk without these keys load with defaults — no migration needed.

### 3.2 Env overrides

Resolved once at `D365Backend.__init__` time and applied on top of the profile:

| Env var               | Profile field        | Type    |
|-----------------------|----------------------|---------|
| `CRM_RETRY_MAX`       | `retry_max`          | int     |
| `CRM_RETRY_BASE_DELAY`| `retry_base_delay`   | float   |
| `CRM_RETRY_MAX_DELAY` | `retry_max_delay`    | float   |
| `CRM_RETRY_JITTER`    | `retry_jitter`       | bool (`0`/`1`/`true`/`false`, case-insensitive) |
| `CRM_ASYNC_TIMEOUT`   | `async_timeout`      | int     |
| `CRM_NO_RETRY`        | shortcut: sets `retry_max = 0` if `1` / `true` | bool |

Parse errors raise `D365Error` at backend construction. Env wins over profile.

### 3.3 Retry algorithm

Inside `D365Backend.request`, replacing the current single `_session.request(...)` call:

```python
attempt = 0
while True:
    try:
        resp = self._session.request(method, url, ...)  # existing args unchanged
    except requests.RequestException as exc:
        if attempt >= self._effective_retry_max or not _is_transport_retryable(exc):
            raise D365Error(f"HTTP transport failure: {exc}") from exc
        delay = _compute_delay(attempt, self.profile, retry_after=None)
        _log_retry(method, url, attempt, delay, reason=str(exc))
        time.sleep(delay)
        attempt += 1
        continue

    if not _is_response_retryable(resp, method):
        return _parse_response(resp, expect_json=expect_json)

    if attempt >= self._effective_retry_max:
        _log_rate_limit_headers(resp, on_429=True)
        return _parse_response(resp, expect_json=expect_json)  # raises D365Error

    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
    delay = _compute_delay(attempt, self.profile, retry_after=retry_after)
    _log_rate_limit_headers(resp, on_429=True)
    _log_retry(method, url, attempt, delay, reason=f"HTTP {resp.status_code}")
    time.sleep(delay)
    attempt += 1
```

`self._effective_retry_max` is set in `__init__` from profile + env. `_parse_retry_after` etc. are private module-level helpers (see §3.4).

### 3.4 Helpers (all module-level, private)

- `_is_transport_retryable(exc) -> bool` — `True` for `requests.exceptions.ConnectionError`, `requests.exceptions.Timeout`, `requests.exceptions.ChunkedEncodingError`. `False` for `SSLError`, `InvalidURL`, `MissingSchema`, `InvalidHeader`. Default `False` for any other `RequestException` subclass not in the allowlist.
- `_is_response_retryable(resp, method) -> bool` — `True` if `resp.status_code == 429`, OR (`resp.status_code in {502, 503, 504}` AND `method in {"GET", "PUT", "PATCH", "DELETE"}`), OR (`resp.status_code == 503` AND `method == "POST"`). Else `False`. POST is intentionally conservative: only retry on explicit overload signals (`429`/`503`), never on `502`/`504`, since the server may have already accepted the create/action.
- `_parse_retry_after(header) -> float | None` — `None` if header missing or unparseable. Tries integer seconds first; falls back to `email.utils.parsedate_to_datetime` for HTTP-date. Negative values clamped to `0.0`.
- `_compute_delay(attempt, profile, retry_after) -> float` — if `retry_after is not None`, return `min(retry_after, profile.retry_max_delay)`. Else `base = min(profile.retry_base_delay * (2 ** attempt), profile.retry_max_delay)`; if `profile.retry_jitter`, return `random.uniform(0, base)` (full jitter, per AWS Architecture Blog "Exponential Backoff And Jitter"); else return `base`.
- `_log_retry(method, url, attempt, delay, reason)` — one line to `sys.stderr`: `[crm] retry {method} {url} attempt={attempt+1}/{effective_max} delay={delay:.1f}s reason={reason}`, where `effective_max == self._effective_retry_max` (resolved from profile + env at `__init__`).
- `_log_rate_limit_headers(resp, *, on_429)` — emit `x-ms-ratelimit-*` headers. `on_429=True`: always log if any rate-limit header is present (the 429 + budget-exhaustion path always logs). `on_429=False`: log only if `os.environ.get("CRM_VERBOSE") == "1"` (the verbose-mode every-response path). Both write to `sys.stderr` as a single line: `[crm] ratelimit time-remaining=N burst-remaining=N retry-after=N`. Headers absent → no line.

### 3.5 Non-retryable failures stay raised

Non-retryable response status (`400`, `401`, `403`, `404`, `5xx` on POST other than `503`) goes straight to `_parse_response`, which raises `D365Error` exactly as today. No behavioral change for the unhappy path.

### 3.6 dry_run preserves behavior

When `self.dry_run` is set, the existing preview-dict return at the top of `D365Backend.request` runs as today — before the retry loop is entered. No HTTP call, no `time.sleep`, no retry preview. Identical behavior to the pre-Spec-B path.

---

## 4. Async-operation polling

### 4.1 Signature

```python
def poll_async_operation(
    self,
    async_operation_id: str,
    *,
    timeout: int | None = None,
    import_job_id: str | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Block until the async operation completes, then return its row.

    Polls asyncoperations(<async_operation_id>) at an increasing interval
    (profile.async_poll_initial → profile.async_poll_max, doubling each tick).
    Returns the final asyncoperation row on success.

    If import_job_id is given, also reads importjobs(<id>).progress on each
    tick and forwards (percent, status_message) to on_progress, if set.

    Raises:
        D365Error on operation failure (statuscode in {31, 32}) or timeout.
    """
```

`timeout=None` means `profile.async_timeout`. Each poll is a normal `self.get(...)` and benefits from §3 retry logic — so a 429 mid-poll is handled transparently.

### 4.2 Poll loop

```python
deadline = time.monotonic() + (timeout or self.profile.async_timeout)
interval = self.profile.async_poll_initial
while True:
    op = cast(dict[str, Any], self.get(f"asyncoperations({async_operation_id})"))
    state = op.get("statecode")
    status = op.get("statuscode")

    if import_job_id is not None and on_progress is not None:
        job = cast(dict[str, Any], self.get(
            f"importjobs({import_job_id})",
            params={"$select": "progress,solutionname,startedon,completedon"},
        ))
        on_progress(float(job.get("progress") or 0.0), op.get("message") or "")

    if state == 3:                # Completed
        if status == 30:          # Succeeded
            return op
        raise D365Error(
            f"Async operation {async_operation_id} ended with statuscode={status}: "
            f"{op.get('friendlymessage') or op.get('message') or '(no message)'}",
            status=status,
            response_body=op,
        )

    if time.monotonic() >= deadline:
        raise D365Error(
            f"Async operation {async_operation_id} did not complete within "
            f"{timeout or self.profile.async_timeout}s (last statecode={state})",
            response_body=op,
        )

    time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    interval = min(interval * 2, self.profile.async_poll_max)
```

State + status reference: AsyncOperation `statecode` values are `0=Ready, 1=Suspended, 2=Locked, 3=Completed`. `statuscode` on a completed row is `30=Succeeded, 31=Failed, 32=Cancelled`. Any other completed status is treated as a failure with the message from the row.

### 4.3 Progress reporting in core/solution.py

`import_solution` constructs a default `on_progress` that writes one line per tick to `sys.stderr` (rate-limited to one line per second, deduplicated when percent does not change):

```
[crm] import progress=15.0% status=Processing
```

`export_solution` does not pass `on_progress` (export has no `progress` field — only the AsyncOperation completion state).

---

## 5. `import_solution` rewrite

### 5.1 New signature + return

```python
def import_solution(
    backend: D365Backend,
    zip_path: str | Path,
    *,
    publish_workflows: bool = True,
    overwrite_unmanaged_customizations: bool = True,
    timeout: int | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
```

`timeout=None` → use `backend.profile.async_timeout`. `quiet=True` → suppress the progress-line callback.

Return shape on success:

```python
{
    "import_job_id": "<guid>",          # the GUID the client generated and sent
    "async_operation_id": "<guid>",     # from ImportSolutionAsync response
    "status": "succeeded",
    "progress": 100.0,                  # final value from importjobs row
    "started_on": "2026-05-24T12:00:00Z",
    "completed_on": "2026-05-24T12:00:42Z",
    "duration_ms": 42137,
}
```

### 5.2 Body sent to `ImportSolutionAsync`

```python
import_job_id = _new_guid()
body = {
    "CustomizationFile": encoded,
    "PublishWorkflows": publish_workflows,
    "OverwriteUnmanagedCustomizations": overwrite_unmanaged_customizations,
    "ImportJobId": import_job_id,
}
resp = as_dict(backend.post("ImportSolutionAsync", json_body=body))
async_op_id = resp["AsyncOperationId"]
```

`ImportJobKey` is also in the response (per the Web API spec) but we ignore it — we already know `ImportJobId` because we sent it.

### 5.3 Dry-run

If `resp` contains `_dry_run` (i.e. `backend.dry_run` is set), return early with the dry-run dict plus `{"action": "ImportSolutionAsync", "import_job_id": import_job_id}`. No poll loop runs.

### 5.4 Failure mode

`poll_async_operation` raises `D365Error` on `statuscode != 30`. We re-raise unchanged but wrap the message with `import_job_id` so the user can `crm raw GET importjobs(<id>)` for the full failure XML.

---

## 6. `export_solution` rewrite

### 6.1 New signature + return

The existing keyword args for export flags (`managed`, `export_autonumbering`, etc.) are preserved verbatim. Two new args:

```python
def export_solution(
    backend: D365Backend,
    unique_name: str,
    output_path: str | Path,
    *,
    managed: bool = False,
    # ...existing export_* flags unchanged...
    timeout: int | None = None,
) -> dict[str, Any]:
```

Return shape on success:

```python
{
    "output": "<path>",
    "bytes": 1234567,
    "managed": False,
    "solution": "MySolution",
    "async_operation_id": "<guid>",
    "export_job_id": "<guid>",
    "duration_ms": 12345,
}
```

### 6.2 Three-step flow

```python
# Step 1: kick off async export
body = {"SolutionName": unique_name, "Managed": managed, ...}  # same body as today
resp = as_dict(backend.post("ExportSolutionAsync", json_body=body))
if "_dry_run" in resp:
    return {**resp, "action": "ExportSolutionAsync"}
async_op_id = resp["AsyncOperationId"]
export_job_id = resp["ExportJobId"]

# Step 2: poll
backend.poll_async_operation(async_op_id, timeout=timeout)

# Step 3: download
dl = as_dict(backend.post(
    "DownloadSolutionExportData",
    json_body={"ExportJobId": export_job_id},
))
encoded = dl.get("ExportSolutionFile")
if not encoded:
    raise D365Error("DownloadSolutionExportData returned no ExportSolutionFile payload.")
data = base64.b64decode(encoded)
out.write_bytes(data)
```

### 6.3 Timing

`duration_ms` covers the AsyncOperation portion only (step 1 + step 2). The download (step 3) is a single sync call already covered by the retry loop and is not double-counted.

---

## 7. Rate-limit header surfacing

### 7.1 Default mode

Silent. No output on 2xx responses.

### 7.2 On 429 / retry

Every 429 response logs one stderr line via `_log_rate_limit_headers(resp, on_429=True)` immediately before the retry-sleep line. Headers parsed: `x-ms-ratelimit-time-remaining-xrm-requests`, `x-ms-ratelimit-burst-remaining-xrm-requests`, `x-ms-ratelimit-limit-xrm-requests`, `Retry-After`. Missing headers are omitted from the line (not printed as empty).

### 7.3 Verbose mode

If `CRM_VERBOSE=1` in env, `_log_rate_limit_headers(resp, on_429=False)` runs on every response and logs the same fields when present. This is the only verbose-mode behavior added in Spec B — broader `--verbose` HTTP transcript is Spec E.

### 7.4 No envelope change

`backend.request` return shape is unchanged. No `rate_limit` sibling key. No `last_rate_limit` attribute on the backend. Surfacing is observational (stderr) only.

---

## 8. CLI surface

### 8.1 Existing commands

`crm solution import <zip>` and `crm solution export <name> <out>` keep their existing flags. Two new shared flags:

| Flag           | Default | Effect                                                        |
|----------------|---------|---------------------------------------------------------------|
| `--timeout N`  | (env or profile) | Override `async_timeout` for this invocation (seconds).       |
| `--no-retry`   | off     | Set `retry_max=0` for this invocation (equivalent to `CRM_NO_RETRY=1`). |
| `--quiet`      | off     | (`solution import` only) suppress progress lines on stderr.   |

### 8.2 No new top-level commands

`solution job-status <id>`, `solution job-cancel <id>` are deferred to Spec C alongside the broader `asyncoperations` browse surface.

---

## 9. Testing

### 9.1 Unit tests (`crm/tests/test_resilience.py`, new file)

Pure-Python, no live server. Tests for:

- `_parse_retry_after` — integer, HTTP-date, missing, malformed, negative.
- `_compute_delay` — caps at `retry_max_delay`, honors `retry_after`, jitter on/off produces deterministic bounds under seeded `random`.
- `_is_response_retryable` — full truth-table over `(method, status_code)`.
- `_is_transport_retryable` — each `requests.exceptions.*` subclass.
- Retry loop integration via `unittest.mock` on `requests.Session.request` — verifies attempt count, sleep durations (via patched `time.sleep`), and final raised error on exhaustion.
- `poll_async_operation` — mocked `backend.get` returning a sequence of pending then completed rows; verifies timeout, success path, failure path, progress callback wiring.

### 9.2 Existing solution tests

`test_core.py` tests for `import_solution` / `export_solution` are updated for the new return shape. Mocks are extended to:
- Return `{"AsyncOperationId": "<g>", "ImportJobKey": "<g>"}` from `ImportSolutionAsync`.
- Return a completed `asyncoperations` row on the first poll.
- Return `{"ExportSolutionFile": "<b64>"}` from `DownloadSolutionExportData`.

No live-server (E2E) tests are added in this spec. `ImportSolutionAsync` / `ExportSolutionAsync` / `DownloadSolutionExportData` are part of the on-prem Web API surface in Dynamics 365 CE 9.x (per `https://learn.microsoft.com/en-us/dynamics365/customer-engagement/web-api/importsolutionasync`). PR3 of this spec adds a manual smoke-test note to `crm/tests/TEST.md` for running `crm solution export` + `crm solution import` against the MOCE 9.1.44.15 test box.

### 9.3 Pyright

All new code lands inside the pyright strict zone (per Spec A). CI fails on any new strict error.

---

## 10. PR sequencing

| PR  | Branch                       | Contents                                                                                                                                                              | Risk   |
|-----|------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|
| **PR1** | `feat/spec-b-retry`        | §3 + §7 + new unit tests for the retry loop + `_log_rate_limit_headers`. No CLI flag changes yet. Pure additive — every existing call still works the same on a 2xx-only server. | Low    |
| **PR2** | `feat/spec-b-async-poll`   | §4 + `poll_async_operation` + unit tests for the poll loop. No callers wired up yet. Pure additive.                                                                   | Low    |
| **PR3** | `feat/spec-b-solution-async` | §5 + §6 + §8 (CLI flags) + version bump to **0.3.0** + `CHANGELOG.md` entry + updated solution tests. Breaking return-shape change.                                   | Medium |

Merge order: PR1 → PR2 → PR3. PR2 rebases on PR1; PR3 rebases on PR2.

---

## 11. Out of scope (deferred)

- **Spec C** — `$batch`, `CreateMultiple` / `UpdateMultiple` / `UpsertMultiple`, impersonation (`CallerObjectId` / `MSCRMCallerID`), `MSCRM.SuppressDuplicateDetection`, `MSCRM.BypassCustomPluginExecution`, `If-Match` ETag, `asyncoperations` browse (job-status / job-cancel CLI), optimistic concurrency.
- **Spec D** — Metadata write API: add-attribute, create-relationship (1:N + N:N), global option set CRUD, delete-entity.
- **Spec E** — DX polish: `--verbose` HTTP transcript, structured logs, env-template generator, Kerberos / SSPI, REPL metadata-cache + tab completion, split `cli.py` per command group, `RetrieveTotalRecordCount`, `metadata list-actions` / `list-functions`.
- **StageSolution** — staged-upgrade flow. Additive feature; revisit after Spec C lands.
