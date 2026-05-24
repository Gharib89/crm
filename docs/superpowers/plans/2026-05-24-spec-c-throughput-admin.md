# Spec C — Throughput + Admin Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `$batch` endpoint support, `MSCRMCallerID` impersonation, `MSCRM.SuppressDuplicateDetection` / `MSCRM.BypassCustomPluginExecution` admin headers, `asyncoperations` browse commands (`crm async list/get/cancel` + solution aliases), and `If-Match` ETag support on PATCH/DELETE. Ship as two sequential PRs and bump the package to `0.4.0`.

**Architecture:** Two PRs against `main`. PR1 lands all backend plumbing (typed kwargs, header injection, `$batch` helper + multipart codec, async-ops helpers) and unit tests — additive, no CLI surface change. PR2 wires the CLI: admin-header flags on every existing write verb, `--if-match` on `entity update/delete`, new `crm batch` and `crm async` command groups, solution aliases. PR2 also bumps `setup.py` to `0.4.0` and writes the CHANGELOG entry. Merge order is strict: PR1 → PR2.

**Tech Stack:** Python 3.9+, `requests` + `requests_ntlm` for HTTP, Click 8.x for CLI, `pytest` + `requests_mock` for tests, `email.parser` for multipart MIME, pyright (strict on `crm/utils/d365_backend.py` and `crm/core/*`) for type checking.

**Spec reference:** `docs/superpowers/specs/2026-05-24-spec-c-throughput-admin-design.md` (commit `083542c`).

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `crm/core/async_ops.py` | `list_async_operations`, `get_async_operation`, `cancel_async_operation` helpers. Strict-typed. |
| `crm/core/batch.py` | `parse_batch_file`, `render_batch_summary` — JSON-file loader + CLI output rendering. Strict-typed. |
| `crm/tests/test_admin_headers.py` | Unit tests for typed-kwarg header injection, GUID validation, ETag validation, 412 + 403/MissingPrivilege error mapping. |
| `crm/tests/test_batch.py` | Unit tests for multipart assembly, response parsing, `D365Backend.batch()`, and the `crm batch` CLI. |
| `crm/tests/test_async_ops.py` | Unit tests for `list/get/cancel_async_operation` and the `crm async` CLI + solution aliases. |

### Files modified

| Path | Why |
|---|---|
| `crm/utils/d365_backend.py` | Typed admin-header kwargs on `request` + verbs, `etag=` kwarg, header assembly, env defaults, `_resolve_caller_id` / `_resolve_bool_env`, `batch()` method, private multipart helpers, error-mapping for 412 / 403. |
| `crm/utils/d365_types.py` | `BatchOperation`, `BatchResult`, `AsyncOperationRow` TypedDicts. |
| `crm/cli.py` | Admin-header flags (`--as-user`, `--suppress-dup-detection`, `--bypass-plugins`) on every write verb; `--if-match` on `entity update` / `entity delete`; new `crm batch` command; new `crm async` group; `solution job-status` / `solution job-cancel` aliases. |
| `crm/tests/TEST.md` | New entries for `test_admin_headers.py`, `test_batch.py`, `test_async_ops.py`; smoke-test note for `crm batch` + `crm async list` against MOCE. |
| `setup.py` | Bump version from `0.3.0` to `0.4.0` in PR2. |
| `CHANGELOG.md` | Append `0.4.0` section in PR2. |

---

# PR1 — `feat/spec-c-backend`

**Branch:** `feat/spec-c-backend` off `main`.
**Goal:** All backend plumbing — typed kwargs, header injection, ETag, error mapping, `$batch` helper, async-ops helpers, all unit tests. No CLI changes. No version bump.

---

### Task 1: Create branch + add new TypedDicts

**Files:**
- Modify: `crm/utils/d365_types.py`

- [ ] **Step 1: Create branch**

```bash
git switch -c feat/spec-c-backend
```

- [ ] **Step 2: Append the three new TypedDicts to `d365_types.py`**

Open `crm/utils/d365_types.py` and append before the trailing `JsonValue = ...` line (i.e., insert at the end of the type-definition block, immediately before the final `JsonValue = Union[...]` assignment):

```python
class BatchOperation(TypedDict, total=False):
    """One operation inside a $batch request.

    `method` and `url` are required. `body` is required on POST/PATCH and
    rejected on GET/DELETE. `headers` is optional (per-op overrides such
    as `If-Match` or `MSCRMCallerID`). `content_id` is optional;
    consumed only inside changesets for `$<n>` back-references.
    """

    method: str
    url: str
    body: dict[str, Any]
    headers: dict[str, str]
    content_id: str


class BatchResult(TypedDict):
    """One result inside a $batch response, aligned to input order."""

    method: str
    url: str
    status: int
    headers: dict[str, str]
    body: Union[dict[str, Any], str, None]
    error: Union[str, None]


class AsyncOperationRow(TypedDict, total=False):
    """Subset of asyncoperation fields the CLI reads + displays."""

    asyncoperationid: str
    name: str
    messagename: str
    statecode: int
    statuscode: int
    createdon: str
    startedon: str
    completedon: str
    _ownerid_value: str
    errorcode: int
    message: str
    friendlymessage: str
```

- [ ] **Step 3: Commit**

```bash
git add crm/utils/d365_types.py
git commit -m "feat(types): add BatchOperation, BatchResult, AsyncOperationRow TypedDicts"
```

---

### Task 2: Env-resolution helpers for admin headers

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_admin_headers.py` (create)

- [ ] **Step 1: Create the test file with env-resolution tests**

```python
"""Unit tests for Spec C admin headers + ETag.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
    _resolve_caller_id,
    _resolve_bool_env,
)


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


class TestEnvResolution:
    def test_caller_id_from_env_accepts_valid_guid(self, monkeypatch):
        monkeypatch.setenv("CRM_AS_USER", "11111111-2222-3333-4444-555555555555")
        assert _resolve_caller_id() == "11111111-2222-3333-4444-555555555555"

    def test_caller_id_from_env_rejects_invalid_guid(self, monkeypatch):
        monkeypatch.setenv("CRM_AS_USER", "not-a-guid")
        with pytest.raises(D365Error, match="CRM_AS_USER"):
            _resolve_caller_id()

    def test_caller_id_from_env_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("CRM_AS_USER", raising=False)
        assert _resolve_caller_id() is None

    def test_bool_env_true_variants(self, monkeypatch):
        for v in ("1", "true", "True", "yes", "on"):
            monkeypatch.setenv("CRM_SUPPRESS_DUP", v)
            assert _resolve_bool_env("CRM_SUPPRESS_DUP") is True

    def test_bool_env_false_variants(self, monkeypatch):
        for v in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("CRM_SUPPRESS_DUP", v)
            assert _resolve_bool_env("CRM_SUPPRESS_DUP") is False

    def test_bool_env_missing_returns_false(self, monkeypatch):
        monkeypatch.delenv("CRM_SUPPRESS_DUP", raising=False)
        assert _resolve_bool_env("CRM_SUPPRESS_DUP") is False
```

- [ ] **Step 2: Run tests to verify they fail with `ImportError`**

```bash
pytest crm/tests/test_admin_headers.py -v
```

Expected: ImportError on `_resolve_caller_id` / `_resolve_bool_env` (they do not exist yet).

- [ ] **Step 3: Implement the helpers in `d365_backend.py`**

Open `crm/utils/d365_backend.py`. Add `import uuid` next to the existing imports (after `import urllib.parse`):

```python
import uuid
```

Then append, immediately after the existing `_env_truthy` function (around line 487), these two new functions:

```python
def _resolve_caller_id() -> str | None:
    """Resolve CRM_AS_USER env into a validated GUID string or None.

    Raises D365Error if the env value is present but not a valid GUID.
    """
    raw = _os.environ.get("CRM_AS_USER")
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip()
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise D365Error(
            f"CRM_AS_USER must be a GUID; got {value!r}"
        ) from exc
    return value


def _resolve_bool_env(name: str) -> bool:
    """Resolve a boolean-style env var. Empty/unset returns False."""
    return _env_truthy(name)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest crm/tests/test_admin_headers.py::TestEnvResolution -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_admin_headers.py
git commit -m "feat(backend): add CRM_AS_USER/CRM_SUPPRESS_DUP/CRM_BYPASS_PLUGINS env resolution helpers"
```

---

### Task 3: Resolve env defaults at backend construction

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_admin_headers.py`

- [ ] **Step 1: Append the failing test**

Add at the end of `crm/tests/test_admin_headers.py`:

```python
class TestBackendDefaults:
    def test_defaults_resolved_from_env(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        monkeypatch.setenv("CRM_SUPPRESS_DUP", "1")
        monkeypatch.setenv("CRM_BYPASS_PLUGINS", "true")
        b = D365Backend(profile, password="pw")
        assert b._default_caller_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert b._default_suppress_dup is True
        assert b._default_bypass_plugins is True

    def test_defaults_absent_when_env_unset(self, monkeypatch, profile):
        for k in ("CRM_AS_USER", "CRM_SUPPRESS_DUP", "CRM_BYPASS_PLUGINS"):
            monkeypatch.delenv(k, raising=False)
        b = D365Backend(profile, password="pw")
        assert b._default_caller_id is None
        assert b._default_suppress_dup is False
        assert b._default_bypass_plugins is False

    def test_invalid_caller_id_env_raises_at_construction(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "not-a-guid")
        with pytest.raises(D365Error, match="CRM_AS_USER"):
            D365Backend(profile, password="pw")
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_admin_headers.py::TestBackendDefaults -v
```

Expected: 3 failed with `AttributeError` on `_default_caller_id`.

- [ ] **Step 3: Wire env resolution into `D365Backend.__init__`**

Inside `D365Backend.__init__`, immediately after the existing line `self._effective_retry_max = _resolve_retry_max(profile)`, insert:

```python
        self._default_caller_id: str | None = _resolve_caller_id()
        self._default_suppress_dup: bool = _resolve_bool_env("CRM_SUPPRESS_DUP")
        self._default_bypass_plugins: bool = _resolve_bool_env("CRM_BYPASS_PLUGINS")
```

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_admin_headers.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_admin_headers.py
git commit -m "feat(backend): resolve admin-header env defaults at D365Backend construction"
```

---

### Task 4: Typed admin-header kwargs on `request` + verbs

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_admin_headers.py`

- [ ] **Step 1: Append the failing tests**

Add to `crm/tests/test_admin_headers.py`:

```python
class TestHeaderInjection:
    def _mock_ok(self, m, method, path, profile):
        url = f"{profile.api_base}{path}"
        m.request(method, url, json={"value": []}, status_code=200,
                  headers={"Content-Type": "application/json"})
        return url

    def test_caller_id_kwarg_sets_mscrmcallerid(self, backend, profile):
        guid = "11111111-2222-3333-4444-555555555555"
        with requests_mock.Mocker() as m:
            url = self._mock_ok(m, "GET", "accounts", profile)
            backend.get("accounts", caller_id=guid)
            assert m.last_request.headers["MSCRMCallerID"] == guid

    def test_caller_id_invalid_guid_raises_before_http(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="GUID"):
                backend.get("accounts", caller_id="not-a-guid")
            assert m.call_count == 0

    def test_caller_id_kwarg_overrides_env_default(self, monkeypatch, profile):
        monkeypatch.setenv("CRM_AS_USER", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        b = D365Backend(profile, password="pw")
        guid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts", caller_id=guid)
            assert m.last_request.headers["MSCRMCallerID"] == guid

    def test_env_default_applied_when_kwarg_absent(self, monkeypatch, profile):
        env_guid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        monkeypatch.setenv("CRM_AS_USER", env_guid)
        b = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            b.get("accounts")
            assert m.last_request.headers["MSCRMCallerID"] == env_guid

    def test_suppress_dup_detection_kwarg_sets_header(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204,
                   headers={"OData-EntityId": f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)"})
            backend.post("accounts", json_body={"name": "a"},
                         suppress_duplicate_detection=True)
            assert m.last_request.headers["MSCRM.SuppressDuplicateDetection"] == "true"

    def test_bypass_plugins_kwarg_sets_header(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204,
                   headers={"OData-EntityId": f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)"})
            backend.post("accounts", json_body={"name": "a"},
                         bypass_custom_plugin_execution=True)
            assert m.last_request.headers["MSCRM.BypassCustomPluginExecution"] == "true"

    def test_typed_kwargs_win_over_extra_headers(self, backend, profile):
        guid = "11111111-2222-3333-4444-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get(
                "accounts",
                caller_id=guid,
                extra_headers={"MSCRMCallerID": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
            )
            assert m.last_request.headers["MSCRMCallerID"] == guid

    def test_headers_absent_when_neither_kwarg_nor_env(self, monkeypatch, backend, profile):
        for k in ("CRM_AS_USER", "CRM_SUPPRESS_DUP", "CRM_BYPASS_PLUGINS"):
            monkeypatch.delenv(k, raising=False)
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get("accounts")
            assert "MSCRMCallerID" not in m.last_request.headers
            assert "MSCRM.SuppressDuplicateDetection" not in m.last_request.headers
            assert "MSCRM.BypassCustomPluginExecution" not in m.last_request.headers
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_admin_headers.py::TestHeaderInjection -v
```

Expected: failures — `request` does not accept the new kwargs.

- [ ] **Step 3: Update `D365Backend.request` signature and body**

Replace the `def request(...)` signature (currently lines 175–184) with:

```python
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        extra_headers: dict[str, str] | None = None,
        expect_json: bool = True,
        caller_id: str | None = None,
        suppress_duplicate_detection: bool = False,
        bypass_custom_plugin_execution: bool = False,
        etag: str | None = None,
    ) -> dict[str, Any] | str | None:
```

Inside the body, replace the existing header-assembly block (`headers = dict(_DEFAULT_HEADERS); if extra_headers: headers.update(extra_headers)`) with:

```python
        headers = dict(_DEFAULT_HEADERS)
        if extra_headers:
            headers.update(extra_headers)

        effective_caller = caller_id if caller_id is not None else self._default_caller_id
        if effective_caller is not None:
            try:
                uuid.UUID(effective_caller)
            except ValueError as exc:
                raise D365Error(
                    f"Invalid GUID for caller_id: {effective_caller!r}"
                ) from exc
            headers["MSCRMCallerID"] = effective_caller

        if suppress_duplicate_detection or self._default_suppress_dup:
            headers["MSCRM.SuppressDuplicateDetection"] = "true"

        if bypass_custom_plugin_execution or self._default_bypass_plugins:
            headers["MSCRM.BypassCustomPluginExecution"] = "true"
```

(Leave the ETag block for Task 5.)

- [ ] **Step 4: Update convenience verbs to forward the new kwargs**

Replace the four convenience verbs (`get`, `post`, `patch`, `delete`) with:

```python
    def get(self, path: str, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any = None, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("POST", path, json_body=json_body, **kw)

    def patch(self, path: str, json_body: Any = None, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("PATCH", path, json_body=json_body, **kw)

    def delete(self, path: str, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("DELETE", path, expect_json=False, **kw)
```

(`**kw` already forwards arbitrary kwargs, so the four typed kwargs pass through unchanged. No edit needed if the verbs already use `**kw` — verify.)

- [ ] **Step 5: Re-run tests**

```bash
pytest crm/tests/test_admin_headers.py -v
```

Expected: all `TestHeaderInjection` tests pass.

- [ ] **Step 6: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_admin_headers.py
git commit -m "feat(backend): typed kwargs for MSCRMCallerID/SuppressDuplicateDetection/BypassPlugins"
```

---

### Task 5: `etag=` kwarg + validation

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_admin_headers.py`

- [ ] **Step 1: Append the failing tests**

Add to `crm/tests/test_admin_headers.py`:

```python
class TestEtag:
    def test_etag_value_sets_if_match(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.patch(f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                    status_code=204)
            backend.patch(
                "accounts(00000000-0000-0000-0000-000000000001)",
                json_body={"name": "a"},
                etag='W/"123"',
            )
            assert m.last_request.headers["If-Match"] == 'W/"123"'

    def test_etag_star_sets_if_match_star(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.patch(f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                    status_code=204)
            backend.patch(
                "accounts(00000000-0000-0000-0000-000000000001)",
                json_body={"name": "a"},
                etag="*",
            )
            assert m.last_request.headers["If-Match"] == "*"

    def test_etag_on_delete(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.delete(f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                     status_code=204)
            backend.delete(
                "accounts(00000000-0000-0000-0000-000000000001)",
                etag='W/"7"',
            )
            assert m.last_request.headers["If-Match"] == 'W/"7"'

    def test_etag_on_get_raises(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="etag not valid on GET"):
                backend.get("accounts", etag='W/"1"')
            assert m.call_count == 0

    def test_etag_on_post_raises(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="etag not valid on POST"):
                backend.post("accounts", json_body={}, etag='W/"1"')
            assert m.call_count == 0

    def test_etag_empty_raises(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="non-empty"):
                backend.patch("accounts(x)", json_body={}, etag="")
            assert m.call_count == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_admin_headers.py::TestEtag -v
```

Expected: 6 failed.

- [ ] **Step 3: Add ETag handling to `D365Backend.request`**

Immediately after the `MSCRM.BypassCustomPluginExecution` block (added in Task 4) and before the `if self.dry_run:` block, insert:

```python
        if etag is not None:
            if etag == "":
                raise D365Error("etag must be non-empty")
            if method.upper() not in ("PATCH", "DELETE"):
                raise D365Error(f"etag not valid on {method.upper()}")
            headers["If-Match"] = etag
```

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_admin_headers.py::TestEtag -v
```

Expected: 6 passed.

- [ ] **Step 5: Run full test file to confirm no regressions**

```bash
pytest crm/tests/test_admin_headers.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_admin_headers.py
git commit -m "feat(backend): etag kwarg sends If-Match on PATCH/DELETE; rejects other verbs"
```

---

### Task 6: 412 + 403/`prvBypassCustomPlugins` error mapping

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_admin_headers.py`

- [ ] **Step 1: Append the failing tests**

Add to `crm/tests/test_admin_headers.py`:

```python
class TestErrorMapping:
    def test_412_maps_to_precondition_failed(self, backend, profile):
        body = {"error": {"code": "0x80048d04", "message": "Concurrency mismatch"}}
        with requests_mock.Mocker() as m:
            m.patch(
                f"{profile.api_base}accounts(00000000-0000-0000-0000-000000000001)",
                status_code=412, json=body,
            )
            with pytest.raises(D365Error) as exc_info:
                backend.patch(
                    "accounts(00000000-0000-0000-0000-000000000001)",
                    json_body={"name": "a"},
                    etag='W/"1"',
                )
            assert exc_info.value.status == 412
            assert exc_info.value.code == "PreconditionFailed"

    def test_403_with_priv_bypass_maps_to_missing_privilege(self, backend, profile):
        body = {"error": {
            "code": "0x80040220",
            "message": "User does not have prvBypassCustomPluginExecution privilege",
        }}
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=403, json=body)
            with pytest.raises(D365Error) as exc_info:
                backend.post("accounts", json_body={"name": "a"},
                             bypass_custom_plugin_execution=True)
            assert exc_info.value.status == 403
            assert exc_info.value.code == "MissingPrivilege"

    def test_403_without_priv_keyword_keeps_server_code(self, backend, profile):
        body = {"error": {"code": "0x80040220", "message": "Insufficient privileges"}}
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=403, json=body)
            with pytest.raises(D365Error) as exc_info:
                backend.post("accounts", json_body={"name": "a"})
            assert exc_info.value.status == 403
            assert exc_info.value.code == "0x80040220"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_admin_headers.py::TestErrorMapping -v
```

Expected: 2 failed (412 case fails on `code`; 403 case fails on `code`). 3rd test should already pass.

- [ ] **Step 3: Patch `_parse_response` to remap codes**

In `crm/utils/d365_backend.py`, locate the final `raise D365Error(...)` inside `_parse_response` (the error path, around line 384). Replace the two lines:

```python
    raise D365Error(message, status=resp.status_code, code=code, response_body=body)
```

with:

```python
    if resp.status_code == 412:
        code = "PreconditionFailed"
    elif (
        resp.status_code == 403
        and isinstance(message, str)
        and "prvBypassCustomPluginExecution" in message
    ):
        code = "MissingPrivilege"

    raise D365Error(message, status=resp.status_code, code=code, response_body=body)
```

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_admin_headers.py::TestErrorMapping -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_admin_headers.py
git commit -m "feat(backend): map 412 to PreconditionFailed; 403 with prvBypassCustomPluginExecution to MissingPrivilege"
```

---

### Task 7: Multipart-MIME assembly helpers

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_batch.py` (create)

- [ ] **Step 1: Create the test file with a golden-body assembly test**

Create `crm/tests/test_batch.py`:

```python
"""Unit tests for Spec C $batch helper + multipart codec."""
# pyright: basic

from __future__ import annotations

import re
from typing import Any

import pytest
import requests_mock

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
    _assemble_batch_body,
    _parse_batch_response,
)


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


@pytest.fixture
def fixed_boundaries(monkeypatch):
    """Return deterministic uuid.hex values so multipart bodies are byte-stable."""
    counter = {"n": 0}
    names = ["batchXX", "csetXX"]

    class _U:
        @property
        def hex(self) -> str:
            i = counter["n"]
            counter["n"] = (i + 1) % len(names)
            return names[i]

    monkeypatch.setattr("crm.utils.d365_backend.uuid.uuid4", lambda: _U())


class TestAssembly:
    def test_all_get(self, profile, fixed_boundaries):
        ops = [
            {"method": "GET", "url": "accounts?$select=name"},
            {"method": "GET", "url": "contacts(00000000-0000-0000-0000-000000000001)"},
        ]
        body, content_type = _assemble_batch_body(
            ops, profile.api_base, transactional=True,
        )
        assert content_type == "multipart/mixed; boundary=batch_batchXX"
        # GET parts only; no changeset wrapper.
        assert body.count("--batch_batchXX") == 3   # 2 parts + closing
        assert "GET accounts?$select=name HTTP/1.1" in body
        assert "multipart/mixed; boundary=changeset" not in body

    def test_single_changeset(self, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, profile.api_base, transactional=True)
        assert "multipart/mixed; boundary=changeset_csetXX" in body
        # Two write sub-parts inside the changeset.
        assert body.count("--changeset_csetXX") == 3  # 2 parts + closing
        assert "Content-ID: 1" in body
        assert "Content-ID: 2" in body
        assert "POST accounts HTTP/1.1" in body
        assert "PATCH accounts(00000000-0000-0000-0000-000000000001) HTTP/1.1" in body

    def test_mixed_get_then_writes(self, profile, fixed_boundaries):
        ops = [
            {"method": "GET", "url": "accounts"},
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, profile.api_base, transactional=True)
        # 1 GET part + 1 changeset part = 2 top-level parts.
        assert body.count("--batch_batchXX") == 3   # 2 + closing
        assert "multipart/mixed; boundary=changeset_csetXX" in body
        assert "GET accounts HTTP/1.1" in body
        assert "Content-ID: 1" in body
        assert "Content-ID: 2" in body

    def test_non_transactional_flattens(self, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        body, _ = _assemble_batch_body(ops, profile.api_base, transactional=False)
        assert "boundary=changeset" not in body
        # Two top-level parts directly.
        assert body.count("--batch_batchXX") == 3
        assert "POST accounts HTTP/1.1" in body
        assert "PATCH accounts(00000000-0000-0000-0000-000000000001) HTTP/1.1" in body
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_batch.py::TestAssembly -v
```

Expected: ImportError on `_assemble_batch_body` / `_parse_batch_response`.

- [ ] **Step 3: Add the assembly helper to `d365_backend.py`**

Add this private function at module scope, immediately after `_parse_retry_after` (around line 517):

```python
def _format_http_part(op: dict[str, Any], content_id: int | None = None) -> str:
    """Render one operation as an `application/http` MIME part body."""
    method = op["method"].upper()
    url = op["url"]
    extra = op.get("headers") or {}
    lines: list[str] = ["Content-Type: application/http",
                        "Content-Transfer-Encoding: binary"]
    if content_id is not None:
        lines.append(f"Content-ID: {content_id}")
    lines.append("")
    lines.append(f"{method} {url} HTTP/1.1")
    if method in ("POST", "PATCH"):
        lines.append("Content-Type: application/json")
    for hk, hv in extra.items():
        lines.append(f"{hk}: {hv}")
    lines.append("")
    if "body" in op and op["body"] is not None:
        lines.append(json.dumps(op["body"]))
    return "\r\n".join(lines)


def _assemble_batch_body(
    operations: "Sequence[dict[str, Any]]",
    api_base: str,
    *,
    transactional: bool,
) -> tuple[str, str]:
    """Assemble a multipart/mixed batch body. Returns (body_text, content_type)."""
    batch_boundary = f"batch_{uuid.uuid4().hex}"
    out: list[str] = []

    def _emit_top_get(op: dict[str, Any]) -> None:
        out.append(f"--{batch_boundary}")
        out.append(_format_http_part(op))

    def _emit_top_write(op: dict[str, Any]) -> None:
        out.append(f"--{batch_boundary}")
        out.append(_format_http_part(op))

    def _emit_changeset(write_ops: list[dict[str, Any]]) -> None:
        cs_boundary = f"changeset_{uuid.uuid4().hex}"
        out.append(f"--{batch_boundary}")
        out.append(f"Content-Type: multipart/mixed; boundary={cs_boundary}")
        out.append("")
        for i, op in enumerate(write_ops, start=1):
            out.append(f"--{cs_boundary}")
            out.append(_format_http_part(op, content_id=i))
        out.append(f"--{cs_boundary}--")

    write_buffer: list[dict[str, Any]] = []
    for op in operations:
        method = op["method"].upper()
        is_write = method in ("POST", "PATCH", "DELETE")
        if transactional and is_write:
            write_buffer.append(op)
            continue
        if write_buffer:
            _emit_changeset(write_buffer)
            write_buffer = []
        if method == "GET":
            _emit_top_get(op)
        else:
            _emit_top_write(op)
    if write_buffer:
        _emit_changeset(write_buffer)

    out.append(f"--{batch_boundary}--")
    body_text = "\r\n".join(out) + "\r\n"
    return body_text, f"multipart/mixed; boundary={batch_boundary}"
```

Also add the import at the top of the file alongside the existing `Callable, cast`:

```python
from typing import Any, Callable, Sequence, cast
```

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_batch.py::TestAssembly -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_batch.py
git commit -m "feat(backend): multipart \$batch body assembly helper"
```

---

### Task 8: Multipart response parser

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_batch.py`

- [ ] **Step 1: Append the failing tests**

Add to `crm/tests/test_batch.py`:

```python
class TestParseResponse:
    def _build_response_body(self, parts: list[str], boundary: str) -> bytes:
        text = f"--{boundary}\r\n" + f"\r\n--{boundary}\r\n".join(parts) + f"\r\n--{boundary}--\r\n"
        return text.encode("utf-8")

    def test_parses_two_top_level_gets(self):
        body = self._build_response_body([
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"value": [{"name": "a"}]}',

            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"value": [{"name": "b"}]}',
        ], boundary="batchresp")
        ops = [
            {"method": "GET", "url": "accounts"},
            {"method": "GET", "url": "contacts"},
        ]
        results = _parse_batch_response(body, "multipart/mixed; boundary=batchresp", ops)
        assert len(results) == 2
        assert results[0]["status"] == 200
        assert results[0]["body"] == {"value": [{"name": "a"}]}
        assert results[1]["body"] == {"value": [{"name": "b"}]}
        assert all(r["error"] is None for r in results)

    def test_parses_changeset_with_content_id(self):
        cs_part = (
            "Content-Type: multipart/mixed; boundary=cs1\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 1\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://crm.x/api/data/v9.2/accounts(00000000-0000-0000-0000-000000000001)\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 2\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "\r\n"
            "--cs1--"
        )
        body = self._build_response_body([cs_part], boundary="batchresp")
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "PATCH", "url": "accounts(00000000-0000-0000-0000-000000000001)",
             "body": {"name": "b"}},
        ]
        results = _parse_batch_response(body, "multipart/mixed; boundary=batchresp", ops)
        assert len(results) == 2
        assert results[0]["status"] == 204
        assert results[1]["status"] == 204
        assert results[0]["headers"].get("OData-EntityId", "").endswith(
            "accounts(00000000-0000-0000-0000-000000000001)"
        )

    def test_error_populated_on_non_2xx_subpart(self):
        body = self._build_response_body([
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "\r\n"
            "HTTP/1.1 404 Not Found\r\n"
            "Content-Type: application/json\r\n"
            "\r\n"
            '{"error":{"code":"0x80040217","message":"Record not found"}}',
        ], boundary="batchresp")
        ops = [{"method": "GET", "url": "accounts(00000000-0000-0000-0000-000000000099)"}]
        results = _parse_batch_response(body, "multipart/mixed; boundary=batchresp", ops)
        assert results[0]["status"] == 404
        assert "Record not found" in (results[0]["error"] or "")
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_batch.py::TestParseResponse -v
```

Expected: ImportError or NameError on `_parse_batch_response`.

- [ ] **Step 3: Implement the parser**

Append to `crm/utils/d365_backend.py`, after `_assemble_batch_body`:

```python
def _split_mime_parts(body: bytes, boundary: str) -> list[bytes]:
    """Split a multipart body on its boundary, ignoring preamble/epilogue."""
    sep = f"--{boundary}".encode("utf-8")
    chunks = body.split(sep)
    # First chunk is preamble (often empty); last is "--\r\n" epilogue marker.
    parts: list[bytes] = []
    for c in chunks[1:]:
        c = c.lstrip(b"\r\n")
        if c.startswith(b"--"):
            break
        if c.endswith(b"\r\n"):
            c = c[:-2]
        parts.append(c)
    return parts


def _parse_http_subpart(raw: bytes) -> dict[str, Any]:
    """Parse one application/http subpart into a BatchResult dict."""
    # Strip the leading MIME headers (Content-Type: application/http, etc.)
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        return {"method": "", "url": "", "status": 0, "headers": {}, "body": None,
                "error": "malformed batch subpart"}
    mime_headers_raw = raw[:sep].decode("utf-8", errors="replace")
    http_block = raw[sep + 4:]

    # First line of http_block: "HTTP/1.1 <code> <reason>"
    status_sep = http_block.find(b"\r\n")
    if status_sep < 0:
        return {"method": "", "url": "", "status": 0, "headers": {}, "body": None,
                "error": "malformed status line"}
    status_line = http_block[:status_sep].decode("utf-8", errors="replace").strip()
    rest = http_block[status_sep + 2:]
    m = re.match(r"^HTTP/[\d.]+\s+(\d+)", status_line)
    status = int(m.group(1)) if m else 0

    # Parse remaining headers + body
    body_sep = rest.find(b"\r\n\r\n")
    if body_sep < 0:
        header_text = rest.decode("utf-8", errors="replace")
        body_text = ""
    else:
        header_text = rest[:body_sep].decode("utf-8", errors="replace")
        body_text = rest[body_sep + 4:].decode("utf-8", errors="replace").strip()

    headers: dict[str, str] = {}
    for line in header_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()

    parsed_body: Any
    if body_text:
        try:
            parsed_body = json.loads(body_text)
        except ValueError:
            parsed_body = body_text
    else:
        parsed_body = None

    content_id = None
    for line in mime_headers_raw.splitlines():
        if line.lower().startswith("content-id:"):
            content_id = line.split(":", 1)[1].strip()
            break

    error: str | None = None
    if not (200 <= status < 300):
        if isinstance(parsed_body, dict):
            err = parsed_body.get("error")
            if isinstance(err, dict):
                error = str(err.get("message") or f"HTTP {status}")
            else:
                error = f"HTTP {status}"
        else:
            error = f"HTTP {status}: {body_text[:200]}" if body_text else f"HTTP {status}"

    return {
        "method": "",
        "url": "",
        "status": status,
        "headers": headers,
        "body": parsed_body,
        "error": error,
        "_content_id": content_id,
    }


def _parse_batch_response(
    body: bytes,
    content_type: str,
    operations: "Sequence[dict[str, Any]]",
) -> list[dict[str, Any]]:
    """Parse a multipart/mixed $batch response into one BatchResult per input op."""
    m = re.search(r'boundary=([^;\s]+)', content_type)
    if not m:
        raise D365Error(f"Cannot find boundary in $batch response Content-Type: {content_type!r}")
    boundary = m.group(1).strip('"')

    # Walk input ops to learn the order of expected GET parts and changeset write-indexes.
    get_indexes: list[int] = []
    changeset_groups: list[list[int]] = []
    current_group: list[int] = []
    for i, op in enumerate(operations):
        if op["method"].upper() == "GET":
            if current_group:
                changeset_groups.append(current_group)
                current_group = []
            get_indexes.append(i)
        else:
            current_group.append(i)
    if current_group:
        changeset_groups.append(current_group)

    results: list[dict[str, Any] | None] = [None] * len(operations)
    get_cursor = 0
    changeset_cursor = 0

    for part in _split_mime_parts(body, boundary):
        ctype_match = re.search(rb"Content-Type:\s*([^\r\n;]+)", part, re.IGNORECASE)
        ctype_val = ctype_match.group(1).decode("utf-8").strip() if ctype_match else ""
        if ctype_val.lower() == "multipart/mixed":
            # Changeset response
            inner_m = re.search(rb"boundary=([^\r\n;]+)", part, re.IGNORECASE)
            if not inner_m:
                continue
            inner_boundary = inner_m.group(1).decode("utf-8").strip('"')
            if changeset_cursor >= len(changeset_groups):
                continue
            group = changeset_groups[changeset_cursor]
            changeset_cursor += 1
            inner_parts = _split_mime_parts(part, inner_boundary)
            # Match inner parts to group by Content-ID order
            id_map: dict[int, list[int]] = {}
            for sub_idx, inner in enumerate(inner_parts):
                parsed = _parse_http_subpart(inner)
                cid_raw = parsed.get("_content_id")
                try:
                    cid = int(cid_raw) if cid_raw is not None else sub_idx + 1
                except ValueError:
                    cid = sub_idx + 1
                id_map.setdefault(cid, []).append(sub_idx)
                if 0 <= cid - 1 < len(group):
                    op_index = group[cid - 1]
                    parsed["method"] = operations[op_index]["method"]
                    parsed["url"] = operations[op_index]["url"]
                    parsed.pop("_content_id", None)
                    results[op_index] = parsed
        else:
            parsed = _parse_http_subpart(part)
            parsed.pop("_content_id", None)
            if get_cursor < len(get_indexes):
                op_index = get_indexes[get_cursor]
                parsed["method"] = operations[op_index]["method"]
                parsed["url"] = operations[op_index]["url"]
                results[op_index] = parsed
                get_cursor += 1

    # Backfill any missing slots with an error placeholder.
    for i, r in enumerate(results):
        if r is None:
            results[i] = {
                "method": operations[i]["method"],
                "url": operations[i]["url"],
                "status": 0,
                "headers": {},
                "body": None,
                "error": "no matching response part",
            }
    return [r for r in results if r is not None]
```

Add `import re` to the top of the file alongside the other imports if not already present (it isn't — add it after `import random`).

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_batch.py::TestParseResponse -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_batch.py
git commit -m "feat(backend): parse multipart/mixed \$batch responses into ordered BatchResult list"
```

---

### Task 9: `D365Backend.batch()` method

**Files:**
- Modify: `crm/utils/d365_backend.py`
- Test: `crm/tests/test_batch.py`

- [ ] **Step 1: Append the integration test**

Add to `crm/tests/test_batch.py`:

```python
class TestBatchMethod:
    def test_batch_round_trip_writes_only(self, backend, profile, fixed_boundaries):
        ops = [
            {"method": "POST", "url": "accounts", "body": {"name": "a"}},
            {"method": "POST", "url": "contacts", "body": {"firstname": "c"}},
        ]
        resp_body = (
            "--batchresp\r\n"
            "Content-Type: multipart/mixed; boundary=cs1\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 1\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/accounts(11111111-1111-1111-1111-111111111111)\r\n"
            "\r\n"
            "--cs1\r\n"
            "Content-Type: application/http\r\n"
            "Content-Transfer-Encoding: binary\r\n"
            "Content-ID: 2\r\n"
            "\r\n"
            "HTTP/1.1 204 No Content\r\n"
            "OData-EntityId: https://x/contacts(22222222-2222-2222-2222-222222222222)\r\n"
            "\r\n"
            "--cs1--\r\n"
            "--batchresp--\r\n"
        )
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}$batch",
                content=resp_body.encode("utf-8"),
                headers={"Content-Type": "multipart/mixed; boundary=batchresp"},
                status_code=200,
            )
            results = backend.batch(ops)
        assert len(results) == 2
        assert results[0]["status"] == 204
        assert results[1]["status"] == 204
        assert "accounts(11111111-1111-1111-1111-111111111111)" in (
            results[0]["headers"].get("OData-EntityId", "")
        )

    def test_batch_validates_method(self, backend):
        with pytest.raises(D365Error, match="method"):
            backend.batch([{"method": "POKE", "url": "accounts"}])

    def test_batch_requires_url(self, backend):
        with pytest.raises(D365Error, match="url"):
            backend.batch([{"method": "GET"}])

    def test_batch_rejects_body_on_get(self, backend):
        with pytest.raises(D365Error, match="body"):
            backend.batch([{"method": "GET", "url": "accounts", "body": {"x": 1}}])

    def test_batch_rejects_body_on_delete(self, backend):
        with pytest.raises(D365Error, match="body"):
            backend.batch([{"method": "DELETE", "url": "accounts(x)", "body": {"x": 1}}])

    def test_batch_dry_run_returns_preview_without_http(self, profile, fixed_boundaries):
        b = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            preview = b.batch([{"method": "GET", "url": "accounts"}])
            assert m.call_count == 0
        assert isinstance(preview, list)
        assert len(preview) == 1
        assert preview[0]["status"] == 0
        assert preview[0]["error"] is None or preview[0]["error"] == "dry-run"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_batch.py::TestBatchMethod -v
```

Expected: `AttributeError: 'D365Backend' object has no attribute 'batch'`.

- [ ] **Step 3: Add the `batch()` method to `D365Backend`**

Inside `class D365Backend`, immediately after the `delete` method, add:

```python
    def batch(
        self,
        operations: "Sequence[dict[str, Any]]",
        *,
        transactional: bool = True,
        continue_on_error: bool = False,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a list of operations via POST $batch.

        See spec C §4 for transactional semantics, request shape, and
        size/count limits. Returns one BatchResult per input op in input order.
        """
        validated: list[dict[str, Any]] = []
        for i, op in enumerate(operations):
            if "method" not in op or "url" not in op:
                raise D365Error(f"batch op #{i} missing method or url: {op!r}")
            m_upper = op["method"].upper()
            if m_upper not in ("GET", "POST", "PATCH", "DELETE"):
                raise D365Error(f"batch op #{i} invalid method: {op['method']!r}")
            if m_upper in ("GET", "DELETE") and op.get("body") is not None:
                raise D365Error(
                    f"batch op #{i}: body not allowed on {m_upper}"
                )
            validated.append({**op, "method": m_upper})

        if self.dry_run:
            return [
                {
                    "method": op["method"],
                    "url": op["url"],
                    "status": 0,
                    "headers": {},
                    "body": None,
                    "error": "dry-run",
                }
                for op in validated
            ]

        body_text, content_type = _assemble_batch_body(
            validated, self.profile.api_base, transactional=transactional,
        )

        headers = dict(_DEFAULT_HEADERS)
        headers["Content-Type"] = content_type
        if continue_on_error:
            headers["Prefer"] = "odata.continue-on-error"

        effective_timeout = timeout if timeout is not None else self.profile.timeout
        url = self.url_for("$batch")
        try:
            resp = self._session.request(  # pyright: ignore[reportUnknownMemberType]
                "POST", url,
                data=body_text.encode("utf-8"),
                headers=headers,
                timeout=effective_timeout,
            )
        except requests.RequestException as exc:
            raise D365Error(f"HTTP transport failure on \$batch: {exc}") from exc

        if not (200 <= resp.status_code < 300):
            raise D365Error(
                f"\$batch failed: HTTP {resp.status_code}: {resp.text[:500]}",
                status=resp.status_code,
                response_body=resp.text,
            )

        return _parse_batch_response(resp.content, resp.headers.get("Content-Type", ""), validated)
```

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_batch.py -v
```

Expected: all `TestAssembly` + `TestParseResponse` + `TestBatchMethod` pass.

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_batch.py
git commit -m "feat(backend): D365Backend.batch() with validation, transactional default, dry-run"
```

---

### Task 10: `crm/core/async_ops.py` + tests

**Files:**
- Create: `crm/core/async_ops.py`
- Create: `crm/tests/test_async_ops.py`

- [ ] **Step 1: Create the test file**

Create `crm/tests/test_async_ops.py`:

```python
"""Unit tests for Spec C asyncoperations browse helpers."""
# pyright: basic

from __future__ import annotations

from typing import Any

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend
from crm.core import async_ops


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


class TestList:
    def test_list_no_filter(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations",
                  json={"value": [{"asyncoperationid": "a", "messagename": "ImportSolution"}]})
            rows = async_ops.list_async_operations(backend)
            assert len(rows) == 1
            qs = m.last_request.qs
            assert qs.get("$top") == ["50"]
            assert qs.get("$orderby") == ["createdon desc"]
            assert "$filter" not in qs

    def test_list_with_state_filter(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations",
                  json={"value": []})
            async_ops.list_async_operations(backend, state=3)
            qs = m.last_request.qs
            assert qs.get("$filter") == ["statecode eq 3"]

    def test_list_with_combined_filters(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations",
                  json={"value": []})
            async_ops.list_async_operations(
                backend,
                state=0,
                message_name="ImportSolution",
                owner_id="11111111-2222-3333-4444-555555555555",
            )
            qs = m.last_request.qs
            f = qs.get("$filter", [""])[0]
            assert "statecode eq 0" in f
            assert "messagename eq 'ImportSolution'" in f
            assert "_ownerid_value eq 11111111-2222-3333-4444-555555555555" in f
            assert " and " in f


class TestGet:
    def test_get_returns_row(self, backend, profile):
        gid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}asyncoperations({gid})",
                  json={"asyncoperationid": gid, "statecode": 3, "statuscode": 30})
            row = async_ops.get_async_operation(backend, gid)
            assert row["asyncoperationid"] == gid
            assert row["statecode"] == 3


class TestCancel:
    def test_cancel_issues_patch(self, backend, profile):
        gid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.patch(f"{profile.api_base}asyncoperations({gid})", status_code=204)
            async_ops.cancel_async_operation(backend, gid)
            req = m.last_request
            assert req.method == "PATCH"
            body = req.json()
            assert body == {"statecode": 3, "statuscode": 32}


class TestListAll:
    def test_follows_next_link_until_exhausted(self, backend, profile):
        next_url = f"{profile.api_base}asyncoperations?$skiptoken=cookie"
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}asyncoperations",
                json={"value": [{"asyncoperationid": "1"}], "@odata.nextLink": next_url},
            )
            m.get(
                next_url,
                json={"value": [{"asyncoperationid": "2"}]},
            )
            rows = async_ops.list_all_async_operations(backend, page_size=1, max_pages=10)
            assert [r["asyncoperationid"] for r in rows] == ["1", "2"]

    def test_max_pages_caps_pagination(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}asyncoperations",
                json={
                    "value": [{"asyncoperationid": "1"}],
                    "@odata.nextLink": f"{profile.api_base}asyncoperations?$skiptoken=a",
                },
            )
            m.get(
                f"{profile.api_base}asyncoperations?$skiptoken=a",
                json={
                    "value": [{"asyncoperationid": "2"}],
                    "@odata.nextLink": f"{profile.api_base}asyncoperations?$skiptoken=b",
                },
            )
            rows = async_ops.list_all_async_operations(backend, page_size=1, max_pages=2)
            assert [r["asyncoperationid"] for r in rows] == ["1", "2"]
            # 3rd page (skiptoken=b) is not fetched.
            assert m.call_count == 2
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_async_ops.py -v
```

Expected: ImportError on `from crm.core import async_ops`.

- [ ] **Step 3: Create `crm/core/async_ops.py`**

```python
"""Browse + control asyncoperation rows on D365 on-prem.

Reference: https://learn.microsoft.com/power-apps/developer/data-platform/asynchronous-service
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Backend, as_dict

_SELECT = (
    "asyncoperationid,name,messagename,statecode,statuscode,"
    "createdon,startedon,completedon,_ownerid_value,errorcode,"
    "message,friendlymessage"
)


def list_async_operations(
    backend: D365Backend,
    *,
    state: int | None = None,
    message_name: str | None = None,
    owner_id: str | None = None,
    top: int = 50,
    order_by: str = "createdon desc",
) -> list[dict[str, Any]]:
    """List asyncoperation rows. Filters are AND-joined when multiple are set."""
    filters: list[str] = []
    if state is not None:
        filters.append(f"statecode eq {int(state)}")
    if message_name is not None:
        escaped = message_name.replace("'", "''")
        filters.append(f"messagename eq '{escaped}'")
    if owner_id is not None:
        filters.append(f"_ownerid_value eq {owner_id}")

    params: dict[str, Any] = {
        "$select": _SELECT,
        "$top": str(int(top)),
        "$orderby": order_by,
    }
    if filters:
        params["$filter"] = " and ".join(filters)
    result = as_dict(backend.get("asyncoperations", params=params))
    value = result.get("value", [])
    return list(value) if isinstance(value, list) else []


def get_async_operation(
    backend: D365Backend,
    async_operation_id: str,
) -> dict[str, Any]:
    """GET asyncoperations(<id>) and return the row."""
    params = {"$select": _SELECT}
    return as_dict(backend.get(
        f"asyncoperations({async_operation_id})",
        params=params,
    ))


def cancel_async_operation(
    backend: D365Backend,
    async_operation_id: str,
) -> None:
    """PATCH asyncoperations(<id>) to Completed/Cancelled.

    statecode=3 (Completed) + statuscode=32 (Cancelled). Only succeeds for
    state in {0=Ready, 1=Suspended}; server returns 400 otherwise.
    """
    backend.patch(
        f"asyncoperations({async_operation_id})",
        json_body={"statecode": 3, "statuscode": 32},
    )


def list_all_async_operations(
    backend: D365Backend,
    *,
    state: int | None = None,
    message_name: str | None = None,
    owner_id: str | None = None,
    page_size: int = 50,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Paginated variant of list_async_operations: follows @odata.nextLink up to max_pages.

    The first call uses the same `$filter` / `$select` / `$top` shape as
    list_async_operations. Subsequent calls follow the absolute URL in
    @odata.nextLink. Stops when the server stops emitting nextLink or
    when max_pages is reached.
    """
    out: list[dict[str, Any]] = []
    filters: list[str] = []
    if state is not None:
        filters.append(f"statecode eq {int(state)}")
    if message_name is not None:
        escaped = message_name.replace("'", "''")
        filters.append(f"messagename eq '{escaped}'")
    if owner_id is not None:
        filters.append(f"_ownerid_value eq {owner_id}")

    params: dict[str, Any] = {
        "$select": _SELECT,
        "$top": str(int(page_size)),
        "$orderby": "createdon desc",
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    page = as_dict(backend.get("asyncoperations", params=params))
    pages_consumed = 1
    while True:
        value = page.get("value", [])
        if isinstance(value, list):
            out.extend(value)
        next_link = page.get("@odata.nextLink")
        if not isinstance(next_link, str) or not next_link:
            break
        if pages_consumed >= max_pages:
            break
        # Absolute URL — backend.get accepts and returns the full URL via url_for.
        page = as_dict(backend.get(next_link))
        pages_consumed += 1
    return out
```

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_async_ops.py -v
```

Expected: all 5 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/core/async_ops.py crm/tests/test_async_ops.py
git commit -m "feat(core): asyncoperations list/get/cancel helpers"
```

---

### Task 11: Pyright clean + push PR1

**Files:**
- All modified files in PR1

- [ ] **Step 1: Run pyright from repo root**

```bash
pyright
```

Expected: exit code `0`. If any new errors are reported in `crm/utils/d365_backend.py`, `crm/utils/d365_types.py`, `crm/core/async_ops.py`, fix them inline before proceeding — typical fixes: add explicit `cast(...)` for `requests` return types, narrow `Any` returns from `_parse_response`, or add explicit type annotations.

- [ ] **Step 2: Run the full test suite**

```bash
pytest crm/tests -v
```

Expected: 0 failures.

- [ ] **Step 3: Update `crm/tests/TEST.md` with the three new files**

Append (or insert in the test-inventory section) lines describing `test_admin_headers.py`, `test_batch.py`, `test_async_ops.py`. Match the existing inventory style. If `TEST.md` does not exist, skip this step.

- [ ] **Step 4: Commit + push**

```bash
git add crm/tests/TEST.md
git commit -m "docs(tests): index Spec C PR1 unit tests" --allow-empty
git push -u origin feat/spec-c-backend
```

- [ ] **Step 5: Open PR1**

```bash
gh pr create --title "Spec C PR1: backend plumbing for $batch + admin headers + ETag" --body "$(cat <<'EOF'
## Summary
- Typed kwargs (`caller_id`, `suppress_duplicate_detection`, `bypass_custom_plugin_execution`, `etag`) on `D365Backend.request` and verbs; env defaults (`CRM_AS_USER`, `CRM_SUPPRESS_DUP`, `CRM_BYPASS_PLUGINS`)
- `D365Backend.batch()` + `_assemble_batch_body` / `_parse_batch_response` private helpers (implicit changeset for consecutive writes)
- `crm/core/async_ops.py` (`list_async_operations`, `get_async_operation`, `cancel_async_operation`)
- Error-mapping: 412 → `code=PreconditionFailed`; 403 + `prvBypassCustomPluginExecution` → `code=MissingPrivilege`
- New TypedDicts: `BatchOperation`, `BatchResult`, `AsyncOperationRow`
- No CLI changes; no version bump

Spec: `docs/superpowers/specs/2026-05-24-spec-c-throughput-admin-design.md`

## Test plan
- [ ] `pytest crm/tests -v` → green
- [ ] `pyright` → exit 0
EOF
)"
```

---

# PR2 — `feat/spec-c-cli`

**Branch:** `feat/spec-c-cli` off `feat/spec-c-backend` (or off `main` after PR1 merges).
**Goal:** Wire all CLI surfaces, bump to 0.4.0, write CHANGELOG.

---

### Task 12: Branch + decorator helper for admin-header flags

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Create branch**

After PR1 merges:

```bash
git switch main
git pull
git switch -c feat/spec-c-cli
```

(If PR1 has not merged yet, branch from `feat/spec-c-backend` and rebase before opening PR2.)

- [ ] **Step 2: Add a shared decorator + resolver helper near the existing `_handle_d365_error`**

In `crm/cli.py`, immediately after the `_handle_d365_error` function (around line 148), add:

```python
def _admin_header_options(f):
    """Stack `--as-user`, `--suppress-dup-detection`, `--bypass-plugins` flags on a command."""
    f = click.option(
        "--bypass-plugins", is_flag=True, default=False,
        help="Send MSCRM.BypassCustomPluginExecution: true (requires prvBypassCustomPlugins).",
    )(f)
    f = click.option(
        "--suppress-dup-detection", is_flag=True, default=False,
        help="Send MSCRM.SuppressDuplicateDetection: true.",
    )(f)
    f = click.option(
        "--as-user", "as_user", metavar="GUID", default=None,
        help="Impersonate systemuser by GUID via MSCRMCallerID header.",
    )(f)
    return f


def _admin_kwargs(as_user: str | None, suppress_dup_detection: bool,
                  bypass_plugins: bool) -> dict[str, Any]:
    """Resolve admin-header CLI flags into backend kwargs."""
    return {
        "caller_id": as_user,
        "suppress_duplicate_detection": suppress_dup_detection,
        "bypass_custom_plugin_execution": bypass_plugins,
    }
```

- [ ] **Step 3: Commit**

```bash
git add crm/cli.py
git commit -m "feat(cli): admin-header decorator + kwarg resolver"
```

---

### Task 13: Plumb admin kwargs through entity-write helpers

**Files:**
- Modify: `crm/core/entity.py`

- [ ] **Step 1: Modify `entity.create` to accept and forward kwargs**

Replace the body of `entity.create` (lines ~62-96) with:

```python
def create(
    backend: D365Backend,
    entity_set: str,
    payload: dict[str, Any],
    *,
    return_record: bool = True,
    caller_id: str | None = None,
    suppress_duplicate_detection: bool = False,
    bypass_custom_plugin_execution: bool = False,
) -> dict[str, Any]:
    """POST a new record."""
    headers: dict[str, str] = {}
    if return_record:
        headers["Prefer"] = "return=representation"

    result = backend.post(
        entity_set, json_body=payload,
        extra_headers=headers,
        caller_id=caller_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    result_dict = as_dict(result)
    if not result_dict:
        return {}
    if "_dry_run" in result_dict:
        return result_dict
    if return_record:
        return result_dict
    entity_id_url = result_dict.get("_entity_id_url")
    if entity_id_url:
        m = re.search(r"\(([0-9a-fA-F-]{36})\)", entity_id_url)
        if m:
            return {"id": m.group(1), "entity_id_url": entity_id_url}
    return result_dict
```

- [ ] **Step 2: Modify `entity.update`, `entity.upsert`, `entity.delete`, `entity.associate`, `entity.disassociate`, `entity.set_lookup`, `entity.clear_lookup` to accept + forward the same three kwargs**

For each function, add the same three keyword-only parameters and forward them on the `backend.{verb}(...)` call. Use this template (apply to each function preserving the rest of the body):

```python
def update(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    prevent_create: bool = True,
    return_record: bool = False,
    if_match: str | None = None,
    caller_id: str | None = None,
    suppress_duplicate_detection: bool = False,
    bypass_custom_plugin_execution: bool = False,
) -> dict[str, Any]:
    """PATCH an existing record. By default prevents accidental upsert via If-Match: *."""
    headers: dict[str, str] = {}
    if return_record:
        headers["Prefer"] = "return=representation"

    effective_etag: str | None = if_match
    if effective_etag is None and prevent_create:
        effective_etag = "*"

    result = backend.patch(
        _build_record_path(entity_set, record_id),
        json_body=payload,
        extra_headers=headers or None,
        etag=effective_etag,
        caller_id=caller_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return as_dict(result)
```

For `entity.update` specifically: the previous code injected `If-Match: *` via `extra_headers` when `prevent_create=True`. Move that semantics into the new `etag=` kwarg path so the backend's typed-kwarg precedence applies. The `if_match` kwarg lets callers pass an explicit etag (this is what powers the `--if-match` CLI flag).

For `entity.delete`, add `if_match: str | None = None` and forward to `etag=if_match`:

```python
def delete(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    if_match: str | None = None,
    caller_id: str | None = None,
    suppress_duplicate_detection: bool = False,
    bypass_custom_plugin_execution: bool = False,
) -> dict[str, Any]:
    """DELETE a record."""
    result = backend.delete(
        _build_record_path(entity_set, record_id),
        etag=if_match,
        caller_id=caller_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return result if isinstance(result, dict) else {"deleted": True, "id": _normalize_id(record_id)}
```

For `upsert`, `associate`, `disassociate`, `set_lookup`, `clear_lookup`: same three kwargs (`caller_id`, `suppress_duplicate_detection`, `bypass_custom_plugin_execution`). `set_lookup` already delegates to `update`, so forward the kwargs through. `clear_lookup` calls `backend.delete` — forward there.

- [ ] **Step 3: Run the existing entity tests to verify no regressions**

```bash
pytest crm/tests/test_core.py -v -k "entity"
```

Expected: existing tests still pass (kwargs are additive).

- [ ] **Step 4: Commit**

```bash
git add crm/core/entity.py
git commit -m "feat(core): forward caller_id/suppress_dup/bypass_plugins/if_match through entity verbs"
```

---

### Task 14: Wire admin flags into `entity create/update/delete/upsert/associate/disassociate/set-lookup/clear-lookup`

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Modify `entity_create` to stack the decorator and forward kwargs**

Replace the existing `entity_create` decorator stack + body (lines ~329-348) with:

```python
@entity.command("create")
@click.argument("entity_set")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a JSON file with the record body.")
@click.option("--no-return", is_flag=True, help="Don't request the record back; just GUID.")
@_admin_header_options
@pass_ctx
def entity_create(ctx, entity_set, data_json, data_file, no_return,
                  as_user, suppress_dup_detection, bypass_plugins):
    """POST a new record."""
    payload = _load_payload(data_json, data_file)
    try:
        result = entity_mod.create(
            ctx.backend(), entity_set, payload,
            return_record=not no_return,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
    _touch_session(ctx, entity_set)
```

- [ ] **Step 2: Modify `entity_update`**

Replace the existing `entity_update` block with:

```python
@entity.command("update")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False))
@click.option("--allow-create", is_flag=True, help="Permit upsert (skip If-Match header).")
@click.option("--return-record", is_flag=True, help="Ask server to return the updated row.")
@click.option("--if-match", "if_match", metavar="ETAG", default=None,
              help='Optimistic concurrency etag. Example (POSIX): --if-match \'W/"123"\'. '
                   'Use --if-match "*" to require any current version.')
@_admin_header_options
@pass_ctx
def entity_update(ctx, entity_set, record_id, data_json, data_file, allow_create,
                  return_record, if_match, as_user, suppress_dup_detection, bypass_plugins):
    """PATCH an existing record."""
    payload = _load_payload(data_json, data_file)
    try:
        result = entity_mod.update(
            ctx.backend(), entity_set, record_id, payload,
            prevent_create=not allow_create,
            return_record=return_record,
            if_match=if_match,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"updated": True, "id": record_id})
```

- [ ] **Step 3: Modify `entity_delete`**

Replace with:

```python
@entity.command("delete")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--if-match", "if_match", metavar="ETAG", default=None,
              help='Optimistic concurrency etag.')
@click.confirmation_option(prompt="Delete this record?")
@_admin_header_options
@pass_ctx
def entity_delete(ctx, entity_set, record_id, if_match,
                  as_user, suppress_dup_detection, bypass_plugins):
    """DELETE a record."""
    try:
        result = entity_mod.delete(
            ctx.backend(), entity_set, record_id,
            if_match=if_match,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
```

- [ ] **Step 4: Modify `entity_upsert`, `entity_associate`, `entity_disassociate`, `entity_set_lookup`, `entity_clear_lookup` analogously**

For each: add `@_admin_header_options` immediately above `@pass_ctx`, add the three matching parameters to the function signature (`as_user, suppress_dup_detection, bypass_plugins`), and pass `**_admin_kwargs(...)` into the core call.

`entity_upsert` example:

```python
@entity.command("upsert")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False))
@_admin_header_options
@pass_ctx
def entity_upsert(ctx, entity_set, record_id, data_json, data_file,
                  as_user, suppress_dup_detection, bypass_plugins):
    """PATCH with create-if-missing semantics."""
    payload = _load_payload(data_json, data_file)
    try:
        result = entity_mod.upsert(
            ctx.backend(), entity_set, record_id, payload,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"upserted": True, "id": record_id})
```

`entity_associate` example:

```python
@entity.command("associate")
@click.argument("target_set")
@click.argument("target_id")
@click.argument("nav")
@click.argument("related_set")
@click.argument("related_id")
@_admin_header_options
@pass_ctx
def entity_associate(ctx, target_set, target_id, nav, related_set, related_id,
                     as_user, suppress_dup_detection, bypass_plugins):
    """Associate two records via a collection-valued nav property (1:N from one-side or N:N)."""
    try:
        result = entity_mod.associate(
            ctx.backend(), target_set, target_id, nav, related_set, related_id,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
```

Apply the same pattern to `entity_disassociate`, `entity_set_lookup`, `entity_clear_lookup`. Each receives the three new flag params at the tail of its parameter list and passes them via `_admin_kwargs(...)` to the core call.

- [ ] **Step 5: Run the help to verify all entity write commands surface the flags**

```bash
python -m crm entity create --help
python -m crm entity update --help
python -m crm entity delete --help
```

Expected: `--as-user`, `--suppress-dup-detection`, `--bypass-plugins` appear on each. `--if-match` appears on `entity update` + `entity delete` only.

- [ ] **Step 6: Commit**

```bash
git add crm/cli.py
git commit -m "feat(cli): admin-header flags + --if-match on entity write verbs"
```

---

### Task 15: Wire admin flags into `workflow activate/deactivate/run`

**Files:**
- Modify: `crm/core/workflow.py`
- Modify: `crm/cli.py`

- [ ] **Step 1: Add the three kwargs to `workflow.set_workflow_state` and `workflow.execute_workflow`**

Open `crm/core/workflow.py`. For each function (`set_workflow_state`, `execute_workflow`), add three keyword-only parameters and forward them to the underlying `backend.{patch|post}` call:

```python
def set_workflow_state(
    backend: D365Backend,
    workflow_id: str,
    *,
    activate: bool,
    caller_id: str | None = None,
    suppress_duplicate_detection: bool = False,
    bypass_custom_plugin_execution: bool = False,
) -> dict[str, Any]:
    ...  # existing body
    # Locate the existing backend.patch(...) call and add the three kwargs:
    result = backend.patch(
        f"workflows({workflow_id})",
        json_body=body,
        caller_id=caller_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    ...
```

Do the same for `execute_workflow`.

- [ ] **Step 2: Modify the three CLI commands to stack `@_admin_header_options`**

Apply the Task 14 pattern to `workflow_activate`, `workflow_deactivate`, `workflow_run`. Example:

```python
@workflow.command("activate")
@click.argument("workflow_id")
@_admin_header_options
@pass_ctx
def workflow_activate(ctx, workflow_id, as_user, suppress_dup_detection, bypass_plugins):
    """Activate a workflow (statecode=1, statuscode=2)."""
    try:
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=True,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

Repeat for `workflow_deactivate` (passing `activate=False`) and `workflow_run` (passing `target_record_id` through).

- [ ] **Step 3: Run help to confirm flags appear**

```bash
python -m crm workflow activate --help
```

Expected: three admin-header flags shown.

- [ ] **Step 4: Commit**

```bash
git add crm/core/workflow.py crm/cli.py
git commit -m "feat(cli): admin-header flags on workflow activate/deactivate/run"
```

---

### Task 16: `crm/core/batch.py` — JSON file loader

**Files:**
- Create: `crm/core/batch.py`

- [ ] **Step 1: Append the failing CLI/loader tests**

Append to `crm/tests/test_batch.py`:

```python
class TestParseBatchFile:
    def test_parses_valid_list(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text(
            '[{"method": "GET", "url": "accounts"}, '
            '{"method": "POST", "url": "accounts", "body": {"name": "a"}}]',
            encoding="utf-8",
        )
        ops = parse_batch_file(p)
        assert len(ops) == 2
        assert ops[0]["method"] == "GET"
        assert ops[1]["body"] == {"name": "a"}

    def test_rejects_non_list_root(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('{"method": "GET", "url": "x"}', encoding="utf-8")
        with pytest.raises(D365Error, match="list"):
            parse_batch_file(p)

    def test_rejects_invalid_method(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('[{"method": "POKE", "url": "x"}]', encoding="utf-8")
        with pytest.raises(D365Error, match="method"):
            parse_batch_file(p)

    def test_rejects_missing_url(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('[{"method": "GET"}]', encoding="utf-8")
        with pytest.raises(D365Error, match="url"):
            parse_batch_file(p)

    def test_rejects_body_on_get(self, tmp_path):
        from crm.core.batch import parse_batch_file
        p = tmp_path / "batch.json"
        p.write_text('[{"method": "GET", "url": "x", "body": {"a": 1}}]', encoding="utf-8")
        with pytest.raises(D365Error, match="body"):
            parse_batch_file(p)
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_batch.py::TestParseBatchFile -v
```

Expected: ImportError on `crm.core.batch.parse_batch_file`.

- [ ] **Step 3: Create `crm/core/batch.py`**

```python
"""$batch JSON-file loader + result rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import D365Error

_VALID_METHODS = ("GET", "POST", "PATCH", "DELETE")


def parse_batch_file(path: str | Path) -> list[dict[str, Any]]:
    """Load a $batch JSON file and return a validated list of operation dicts."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise D365Error(f"Could not parse {p}: {exc}") from exc
    if not isinstance(data, list):
        raise D365Error(f"{p}: expected a JSON list at root, got {type(data).__name__}")

    out: list[dict[str, Any]] = []
    for i, op in enumerate(data):
        if not isinstance(op, dict):
            raise D365Error(f"{p} op #{i}: expected an object, got {type(op).__name__}")
        method_raw = op.get("method")
        if not isinstance(method_raw, str):
            raise D365Error(f"{p} op #{i}: missing or invalid 'method'")
        method = method_raw.upper()
        if method not in _VALID_METHODS:
            raise D365Error(
                f"{p} op #{i}: invalid method {method_raw!r} "
                f"(must be one of {_VALID_METHODS})"
            )
        url = op.get("url")
        if not isinstance(url, str) or not url:
            raise D365Error(f"{p} op #{i}: missing or empty 'url'")
        if method in ("GET", "DELETE") and op.get("body") is not None:
            raise D365Error(f"{p} op #{i}: body not allowed on {method}")
        validated: dict[str, Any] = {"method": method, "url": url}
        if op.get("body") is not None:
            if not isinstance(op["body"], dict):
                raise D365Error(f"{p} op #{i}: body must be an object")
            validated["body"] = op["body"]
        if op.get("headers") is not None:
            if not isinstance(op["headers"], dict):
                raise D365Error(f"{p} op #{i}: headers must be an object")
            validated["headers"] = op["headers"]
        if op.get("content_id") is not None:
            cid = op["content_id"]
            if not isinstance(cid, str) or not cid:
                raise D365Error(f"{p} op #{i}: content_id must be a non-empty string")
            validated["content_id"] = cid
        out.append(validated)
    return out


def render_batch_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts for human-readable CLI output."""
    total = len(results)
    success = sum(1 for r in results if 200 <= r.get("status", 0) < 300)
    failed = total - success
    return {"total": total, "success": success, "failed": failed}
```

- [ ] **Step 4: Re-run tests**

```bash
pytest crm/tests/test_batch.py::TestParseBatchFile -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add crm/core/batch.py crm/tests/test_batch.py
git commit -m "feat(core): parse_batch_file loader with validation"
```

---

### Task 17: `crm batch` CLI command

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Append the failing CLI test**

Append to `crm/tests/test_batch.py`:

```python
class TestBatchCLI:
    def test_continue_on_error_rejected_in_transactional_mode(self, tmp_path):
        from click.testing import CliRunner
        from crm import cli as crm_cli
        runner = CliRunner()
        p = tmp_path / "b.json"
        p.write_text('[{"method": "GET", "url": "accounts"}]', encoding="utf-8")
        result = runner.invoke(crm_cli.cli, [
            "batch", str(p), "--continue-on-error",
        ], env={"D365_URL": "https://x/y", "D365_USER": "u",
                "D365_PASSWORD": "p", "D365_DOMAIN": "d"})
        assert result.exit_code != 0
        assert "continue-on-error" in result.output.lower() or "transaction" in result.output.lower()
```

- [ ] **Step 2: Add the `crm batch` command to `cli.py`**

Add a top-level command immediately after the `cli.command("service-document")` block (around line 954). Also add `from crm.core import batch as batch_mod` to the top-of-file import group:

```python
from crm.core import (
    async_ops as async_ops_mod,
    batch as batch_mod,
    connection as conn_mod,
    entity as entity_mod,
    export as export_mod,
    metadata as meta_mod,
    query as query_mod,
    session as session_mod,
    solution as sol_mod,
    workflow as workflow_mod,
)
```

The new command:

```python
@cli.command("batch")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-transaction", is_flag=True, default=False,
              help="Send each op as a top-level operation; no changeset wrapping.")
@click.option("--continue-on-error", is_flag=True, default=False,
              help="Send Prefer: odata.continue-on-error (requires --no-transaction).")
@click.option("--output", "output_path", type=click.Path(dir_okay=False), default=None,
              help="Write BatchResult[] JSON to this path.")
@click.option("--timeout", type=int, default=None,
              help="Override request timeout (seconds) for the batch call.")
@pass_ctx
def cli_batch(ctx, file_path, no_transaction, continue_on_error, output_path, timeout):
    """Execute a $batch from a JSON file."""
    if continue_on_error and not no_transaction:
        ctx.emit(False, error=(
            "--continue-on-error requires --no-transaction; "
            "Prefer: odata.continue-on-error is meaningless inside a changeset."
        ))
        sys.exit(2)
    try:
        ops = batch_mod.parse_batch_file(file_path)
        results = ctx.backend().batch(
            ops,
            transactional=not no_transaction,
            continue_on_error=continue_on_error,
            timeout=timeout,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    if output_path:
        Path(output_path).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        ctx.emit(True, data={"written": output_path,
                             **batch_mod.render_batch_summary(results)})
    else:
        ctx.emit(True, data=results, meta=batch_mod.render_batch_summary(results))
```

- [ ] **Step 3: Run CLI test**

```bash
pytest crm/tests/test_batch.py::TestBatchCLI -v
```

Expected: 1 passed.

- [ ] **Step 4: Verify help renders**

```bash
python -m crm batch --help
```

Expected: usage block including `--no-transaction`, `--continue-on-error`, `--output`, `--timeout`.

- [ ] **Step 5: Commit**

```bash
git add crm/cli.py crm/tests/test_batch.py
git commit -m "feat(cli): crm batch <file.json> command"
```

---

### Task 18: `crm async list/get/cancel` command group

**Files:**
- Modify: `crm/cli.py`

- [ ] **Step 1: Append failing CLI tests**

Append to `crm/tests/test_async_ops.py`:

```python
class TestAsyncCLI:
    def test_async_list_help(self):
        from click.testing import CliRunner
        from crm import cli as crm_cli
        runner = CliRunner()
        result = runner.invoke(crm_cli.cli, ["async", "list", "--help"])
        assert result.exit_code == 0
        assert "--state" in result.output
        assert "--message" in result.output

    def test_async_list_state_resolves_named_value(self, monkeypatch, profile):
        from click.testing import CliRunner
        from crm import cli as crm_cli

        captured: dict[str, Any] = {}

        def fake_list(backend, **kw):
            captured.update(kw)
            return []

        monkeypatch.setattr("crm.core.async_ops.list_async_operations", fake_list)
        monkeypatch.setattr("crm.cli.CLIContext.backend",
                            lambda self: object())  # dummy backend; fake_list ignores it
        runner = CliRunner()
        result = runner.invoke(crm_cli.cli, ["async", "list", "--state", "ready"])
        assert result.exit_code == 0, result.output
        assert captured["state"] == 0

    def test_solution_job_status_alias(self, monkeypatch):
        from click.testing import CliRunner
        from crm import cli as crm_cli

        called: dict[str, Any] = {}

        def fake_get(backend, async_id):
            called["async_id"] = async_id
            return {"asyncoperationid": async_id, "statecode": 3, "statuscode": 30}

        monkeypatch.setattr("crm.core.async_ops.get_async_operation", fake_get)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        runner = CliRunner()
        result = runner.invoke(crm_cli.cli, [
            "solution", "job-status", "11111111-1111-1111-1111-111111111111",
        ])
        assert result.exit_code == 0, result.output
        assert called["async_id"] == "11111111-1111-1111-1111-111111111111"
```

- [ ] **Step 2: Add the `async` group**

In `cli.py`, immediately before the workflow group (around line 1101), insert:

```python
# ── Async-operations group ──────────────────────────────────────────────

_ASYNC_STATE_NAMES = {
    "ready": 0,
    "suspended": 1,
    "locked": 2,
    "completed": 3,
}


def _resolve_async_state(value: str | None) -> int | None:
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    name = value.lower()
    if name in _ASYNC_STATE_NAMES:
        return _ASYNC_STATE_NAMES[name]
    raise click.BadParameter(
        f"--state must be one of {sorted(_ASYNC_STATE_NAMES)} or an integer; got {value!r}"
    )


@cli.group("async")
def async_group():
    """List, inspect, and cancel asynchronous operations."""


@async_group.command("list")
@click.option("--state", default=None,
              help="ready | suspended | locked | completed | <int>")
@click.option("--message", "message_name", default=None,
              help="Filter by messagename (e.g. ImportSolution).")
@click.option("--owner", "owner_id", default=None,
              help="Filter by systemuser GUID.")
@click.option("--top", type=int, default=50, help="Page size per call (default 50).")
@click.option("--all", "fetch_all", is_flag=True, default=False,
              help="Follow @odata.nextLink until exhausted (caps at --max-pages).")
@click.option("--max-pages", type=int, default=20,
              help="Safety cap on pagination depth when --all is set (default 20).")
@pass_ctx
def async_list(ctx, state, message_name, owner_id, top, fetch_all, max_pages):
    """List asyncoperation rows."""
    try:
        state_int = _resolve_async_state(state)
        backend = ctx.backend()
        rows = async_ops_mod.list_async_operations(
            backend, state=state_int, message_name=message_name,
            owner_id=owner_id, top=top,
        )
        if fetch_all:
            rows = async_ops_mod.list_all_async_operations(
                backend, state=state_int, message_name=message_name,
                owner_id=owner_id, page_size=top, max_pages=max_pages,
            )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=rows, meta={"count": len(rows)})


@async_group.command("get")
@click.argument("async_operation_id")
@pass_ctx
def async_get(ctx, async_operation_id):
    """Get one asyncoperation row."""
    try:
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=row)


@async_group.command("cancel")
@click.argument("async_operation_id")
@click.confirmation_option(prompt="Cancel this async operation?")
@pass_ctx
def async_cancel(ctx, async_operation_id):
    """Cancel a pending or suspended asyncoperation."""
    try:
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"cancelled": True, "id": async_operation_id})
```

- [ ] **Step 3: Add `solution job-status` and `solution job-cancel` aliases**

In `cli.py`, find the `@cli.group()` block for `solution()` (around line 805) and append two new commands to it, right after the existing `solution_publish` (around line 951):

```python
@solution.command("job-status")
@click.argument("async_operation_id")
@pass_ctx
def solution_job_status(ctx, async_operation_id):
    """Alias for `crm async get <id>` — inspect a solution import/export job."""
    try:
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=row)


@solution.command("job-cancel")
@click.argument("async_operation_id")
@click.confirmation_option(prompt="Cancel this job?")
@pass_ctx
def solution_job_cancel(ctx, async_operation_id):
    """Alias for `crm async cancel <id>`."""
    try:
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"cancelled": True, "id": async_operation_id})
```

- [ ] **Step 4: Run CLI tests**

```bash
pytest crm/tests/test_async_ops.py::TestAsyncCLI -v
```

Expected: 3 passed.

- [ ] **Step 5: Verify help**

```bash
python -m crm async list --help
python -m crm async get --help
python -m crm async cancel --help
python -m crm solution job-status --help
```

- [ ] **Step 6: Commit**

```bash
git add crm/cli.py crm/tests/test_async_ops.py
git commit -m "feat(cli): crm async list/get/cancel + solution job-status/job-cancel aliases"
```

---

### Task 19: Version bump + CHANGELOG + docs

**Files:**
- Modify: `setup.py`
- Modify: `CHANGELOG.md`
- Modify: `crm/tests/TEST.md` (if present)

- [ ] **Step 1: Bump `setup.py` version**

In `setup.py`, change the `version=` argument from `"0.3.0"` to `"0.4.0"`.

- [ ] **Step 2: Prepend new CHANGELOG section**

Open `CHANGELOG.md`. Immediately after the leading metadata lines (before the existing `## [0.3.0]` block), insert:

```markdown
## [0.4.0] — 2026-05-24

This release lands Spec C from the post-code-review roadmap: `$batch`
support, on-prem-correct impersonation via `MSCRMCallerID`, two admin
headers for write paths, an `asyncoperations` browse surface, and
explicit optimistic concurrency via `If-Match`. See
`docs/superpowers/specs/2026-05-24-spec-c-throughput-admin-design.md`
for the full design.

### Added

- `D365Backend.batch(operations, *, transactional=True, continue_on_error=False, timeout=None)` — execute a list of operations via POST `$batch`. Consecutive writes are auto-grouped into one changeset; GETs go as top-level operations.
- `crm batch <file.json>` CLI command with `--no-transaction`, `--continue-on-error`, `--output`, `--timeout` flags.
- Backend typed kwargs on every verb: `caller_id`, `suppress_duplicate_detection`, `bypass_custom_plugin_execution`, `etag`. Env defaults: `CRM_AS_USER`, `CRM_SUPPRESS_DUP`, `CRM_BYPASS_PLUGINS`.
- Per-command CLI flags on every write/action verb: `--as-user <guid>`, `--suppress-dup-detection`, `--bypass-plugins`. `--if-match <etag>` on `entity update` and `entity delete`.
- `crm async list/get/cancel` plus `crm solution job-status / job-cancel` aliases.
- New TypedDicts: `BatchOperation`, `BatchResult`, `AsyncOperationRow`.

### Changed

- HTTP `412` responses now map to `D365Error(code="PreconditionFailed")`.
- HTTP `403` responses whose body references `prvBypassCustomPluginExecution` map to `D365Error(code="MissingPrivilege")`.

### Deferred

- `CreateMultiple` / `UpdateMultiple` / `UpsertMultiple` — Dataverse cloud only; not present on MOCE 9.1.x on-prem.
- `CallerObjectId` impersonation header — requires Microsoft Entra ID; on-prem AD users use `MSCRMCallerID`.
- Server-side `$batch` size limits (typical Dataverse: 100 changesets per batch; 1000 ops per changeset) are not enforced client-side; the server's `MaxBatchSize` / `MaxChangesetSize` error surfaces verbatim.

### Notes for callers

- `POST $batch` is retried only on `429` and `503` (Spec B conservative-POST policy). A retried batch re-sends the assembled body verbatim — idempotency is the caller's responsibility.

```

- [ ] **Step 3: Add a smoke-test note to `crm/tests/TEST.md` (if it exists)**

Append a section noting that `crm batch sample.json` and `crm async list --top 5` should be smoke-tested against MOCE 9.1.44.15 once the PR2 build is available. Skip if no `TEST.md`.

- [ ] **Step 4: Run the whole test suite + pyright**

```bash
pytest crm/tests -v
pyright
```

Expected: 0 test failures; pyright exit 0.

- [ ] **Step 5: Commit + push**

```bash
git add setup.py CHANGELOG.md crm/tests/TEST.md
git commit -m "release: bump to 0.4.0; CHANGELOG for Spec C"
git push -u origin feat/spec-c-cli
```

- [ ] **Step 6: Open PR2**

```bash
gh pr create --title "Spec C PR2: CLI surface + 0.4.0" --body "$(cat <<'EOF'
## Summary
- `crm batch <file.json>` with `--no-transaction` / `--continue-on-error` / `--output` / `--timeout`
- `crm async list/get/cancel` command group + `crm solution job-status / job-cancel` aliases
- Admin-header flags (`--as-user`, `--suppress-dup-detection`, `--bypass-plugins`) on every existing write verb in the `entity` and `workflow` groups
- `--if-match <etag>` on `entity update` and `entity delete`
- Bumps to **0.4.0**; new CHANGELOG section

Spec: `docs/superpowers/specs/2026-05-24-spec-c-throughput-admin-design.md`. Builds on PR1 (`feat/spec-c-backend`).

## Test plan
- [ ] `pytest crm/tests -v` → green
- [ ] `pyright` → exit 0
- [ ] `python -m crm batch sample.json` smoke-test against MOCE 9.1.44.15
- [ ] `python -m crm async list --top 5` smoke-test
EOF
)"
```

---

## Self-review checklist

After all tasks are executed:

1. **Spec coverage:** Every spec section is implemented:
   - §1 (Goals + non-goals) — fully reflected by the PR set.
   - §2 (Architecture) — Task 1 (TypedDicts), Tasks 4–5 (typed kwargs + ETag).
   - §3 (Admin headers) — Tasks 2–5 + Task 12 + Task 14 + Task 15.
   - §4 ($batch) — Tasks 7–9 (backend), Task 16 (loader), Task 17 (CLI).
   - §5 (asyncoperations browse) — Task 10 (helpers), Task 18 (CLI).
   - §6 (ETag) — Task 5 (backend), Task 13 (`if_match` plumbing), Task 14 (CLI flag).
   - §7 (Testing) — every backend helper has unit tests; CLI tests in Tasks 17–18; pyright in Task 11 + Task 19.
   - §8 (PR sequencing) — exactly two PRs as designed.
2. **No placeholders.** Every code step shows complete code.
3. **Type consistency.** TypedDict names (`BatchOperation`, `BatchResult`, `AsyncOperationRow`) used identically across backend, core, and CLI tasks. Kwarg names (`caller_id`, `suppress_duplicate_detection`, `bypass_custom_plugin_execution`, `etag`, `if_match`) used identically across all forwarding sites.
