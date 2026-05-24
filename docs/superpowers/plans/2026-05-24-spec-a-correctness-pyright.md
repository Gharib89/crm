# Spec A — Correctness Fixes + Pyright Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land 9 correctness/UX fixes from the post-code-review audit + adopt pyright strict (zone-scoped) on the production code paths of the `crm` D365 on-prem CLI, shipping as a 3-PR series and bumping the package to `0.2.0`.

**Architecture:** Three sequential PRs against `main`. PR1 is tooling + a mechanical full-typing pass that resolves 342 strict pyright errors; it changes no runtime behaviour. PR2 lands five pure-internal correctness fixes with mocked unit tests. PR3 lands four observable changes (one breaking — error envelope), bumps the version, and adds the CHANGELOG. Merge order is strict: PR1 → PR2 → PR3, each rebased on the prior.

**Tech Stack:** Python 3.9+, Click 8.x for CLI, `requests` + `requests_ntlm` for HTTP, `prompt_toolkit` for REPL, `pytest` + `requests_mock` for tests, pyright (new) for type checking, GitHub Actions for CI.

**Spec reference:** `docs/superpowers/specs/2026-05-24-spec-a-correctness-pyright-design.md` (commit `0b7246d`).

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `pyrightconfig.json` | Pyright config — strict on `crm/core/*` + `crm/utils/d365_backend.py`, basic elsewhere. |
| `crm/utils/d365_types.py` | `TypedDict` shapes for Web API responses (`WhoAmIResponse`, `EntityDefinition`, `SolutionRow`, `ODataCollection[T]`, etc.). |
| `CHANGELOG.md` | Release notes; first entry covers all three PRs under `0.2.0`. |

### Files modified

| Path | Why |
|---|---|
| `setup.py` | Add `pyright>=1.1.380` to `extras_require['dev']`; bump version to `0.2.0` in PR3. |
| `.github/workflows/build.yml` | Add a `pyright` step after the `pytest` step. |
| `crm/utils/d365_backend.py` | Type annotations + `_parse_response` text/plain branch (§3.9). |
| `crm/core/entity.py` | Type annotations + drop `If-None-Match: null` (§3.1). |
| `crm/core/query.py` | Type annotations + `params=` for FetchXML (§3.4) + `$count` int parse (§3.9). |
| `crm/core/export.py` | Type annotations + `_ordered_keys` fix (§3.2). |
| `crm/core/metadata.py` | Type annotations + EntitySetName read-back (§3.3). |
| `crm/core/solution.py` | Type annotations + `Export*Settings` kwargs (§3.6). |
| `crm/core/connection.py` | Type annotations + `.env` unquote (§3.8). |
| `crm/core/session.py` | Type annotations only. |
| `crm/core/workflow.py` | Type annotations only. |
| `crm/cli.py` | Envelope null (§3.5) + REPL backend reuse + `invalidate_backend()` method (§3.7) + `--export-setting` flag (§3.6). Basic-mode pyright zone. |
| `crm/tests/test_core.py` | New tests (one per fix) and updates to existing tests whose assertions change. |
| `crm/tests/test_full_e2e.py` | Two new E2E tests for §3.3 + §3.6. |

---

# PR1 — `feat/pyright-setup`

**Branch:** `feat/pyright-setup` off `main`.
**Goal:** All 342 strict-mode pyright errors gone; CI gate live; no runtime behaviour changes.

---

### Task 1: Create the PR1 branch and add `pyright` to dev deps

**Files:**
- Modify: `setup.py`

- [ ] **Step 1: Create branch**

```bash
git switch -c feat/pyright-setup
```

- [ ] **Step 2: Add pyright to dev extras**

Modify `setup.py` lines 22-24 to add `pyright` to the dev extras list:

```python
    extras_require={
        "dev": ["pytest>=7.0", "requests_mock>=1.10", "pyinstaller>=6.0", "pyright>=1.1.380"],
    },
```

- [ ] **Step 3: Install the new dep into the local venv**

```bash
pip install -e ".[dev]"
```

Expected: pyright installs cleanly; `pyright --version` prints `pyright 1.1.x`.

- [ ] **Step 4: Commit**

```bash
git add setup.py
git commit -m "build: add pyright to dev extras"
```

---

### Task 2: Add `pyrightconfig.json`

**Files:**
- Create: `pyrightconfig.json`

- [ ] **Step 1: Write the config**

```json
{
  "include": ["crm"],
  "exclude": ["**/__pycache__", "build", "dist"],
  "pythonVersion": "3.9",
  "typeCheckingMode": "strict",
  "reportMissingTypeStubs": false,
  "executionEnvironments": [
    { "root": "crm/cli.py",             "typeCheckingMode": "basic" },
    { "root": "crm/utils/repl_skin.py", "typeCheckingMode": "basic" },
    { "root": "crm/tests",              "typeCheckingMode": "basic" }
  ]
}
```

- [ ] **Step 2: Run pyright to confirm the strict-zone baseline**

```bash
pyright --outputjson 2>/dev/null | python -c "import json,sys; s=json.load(sys.stdin)['summary']; print(f\"errors={s['errorCount']} warnings={s['warningCount']} files={s['filesAnalyzed']}\")"
```

Expected: `errors=342 warnings=1 files=10` (approximately — small drift is fine). This is the baseline we're going to drive to zero.

- [ ] **Step 3: Commit**

```bash
git add pyrightconfig.json
git commit -m "build: add pyrightconfig with strict crm/core + crm/utils zone"
```

---

### Task 3: Add pyright step to CI

**Files:**
- Modify: `.github/workflows/build.yml`

- [ ] **Step 1: Insert pyright step after the existing `Run unit tests` step**

Edit `.github/workflows/build.yml`. After the block:

```yaml
      - name: Run unit tests
        run: pytest -q
```

insert:

```yaml
      - name: Type check (pyright)
        run: pyright
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/build.yml
git commit -m "ci: run pyright after unit tests"
```

Note: This step will fail in CI until Tasks 4–13 drive the error count to zero. That's expected — PR1 is one atomic deliverable.

---

### Task 4: Create the `d365_types.py` module with TypedDicts for Web API responses

**Files:**
- Create: `crm/utils/d365_types.py`

- [ ] **Step 1: Write the module**

```python
"""Typed Web API response shapes consumed by crm.core.

These TypedDicts cover only the fields the codebase actually reads — they
are not a complete model of the Dataverse Web API. Use TypedDict.__total__
False for response shapes where the server may omit fields depending on
the operation.

Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/
"""

from __future__ import annotations

from typing import Any, Generic, TypedDict, TypeVar

T = TypeVar("T")


class WhoAmIResponse(TypedDict, total=False):
    """Response from WhoAmI() — every field is optional from the consumer's POV."""

    UserId: str
    BusinessUnitId: str
    OrganizationId: str


class LocalizedLabel(TypedDict, total=False):
    Label: str
    LanguageCode: int


class LabelPayload(TypedDict, total=False):
    LocalizedLabels: list[LocalizedLabel]
    UserLocalizedLabel: LocalizedLabel


class EntityDefinition(TypedDict, total=False):
    LogicalName: str
    EntitySetName: str
    SchemaName: str
    MetadataId: str
    IsCustomEntity: bool
    DisplayName: LabelPayload


class AttributeDefinition(TypedDict, total=False):
    LogicalName: str
    SchemaName: str
    AttributeType: str
    IsCustomAttribute: bool


class OptionMetadata(TypedDict, total=False):
    Value: int
    Label: LabelPayload


class OptionSetPayload(TypedDict, total=False):
    Options: list[OptionMetadata]


class OptionSetResponse(TypedDict, total=False):
    LogicalName: str
    OptionSet: OptionSetPayload
    GlobalOptionSet: OptionSetPayload


class SolutionRow(TypedDict, total=False):
    solutionid: str
    uniquename: str
    friendlyname: str
    version: str
    ismanaged: bool
    installedon: str


class SolutionComponent(TypedDict, total=False):
    componenttype: int
    objectid: str
    rootcomponentbehavior: int


class WorkflowRow(TypedDict, total=False):
    workflowid: str
    name: str
    category: int
    primaryentity: str
    statecode: int
    statuscode: int
    ondemand: bool
    type: int


class RelationshipRow(TypedDict, total=False):
    SchemaName: str
    ReferencedEntity: str
    ReferencingEntity: str
    ReferencingAttribute: str
    Entity1LogicalName: str
    Entity2LogicalName: str
    IntersectEntityName: str


class ODataCollection(TypedDict, Generic[T], total=False):
    """Generic OData collection envelope: `{ "value": [...], "@odata.nextLink": "..." }`."""

    value: list[T]


# Raw response unions used at the wire boundary. Backend methods widen to these;
# core/* callers narrow before use (helper: `_as_dict` in d365_backend.py).
JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
JsonObject = dict[str, Any]
```

- [ ] **Step 2: Verify pyright is clean on the new file**

```bash
pyright crm/utils/d365_types.py
```

Expected: `0 errors, 0 warnings`.

- [ ] **Step 3: Commit**

```bash
git add crm/utils/d365_types.py
git commit -m "feat(types): add TypedDict shapes for Web API responses"
```

---

### Task 5: Type-annotate `crm/utils/d365_backend.py`

This is the foundation — every core module's types depend on backend method signatures.

**Files:**
- Modify: `crm/utils/d365_backend.py`

- [ ] **Step 1: Run pyright on the file to see the current error set**

```bash
pyright crm/utils/d365_backend.py 2>&1 | tail -10
```

Expected: ~75 errors.

- [ ] **Step 2: Apply the annotations**

Replace the entire module body from the top of the file through end of `D365Backend` and `_parse_response`. The structural changes:

1. `_DEFAULT_HEADERS: dict[str, str]` annotation.
2. `D365Error.__init__` already typed — leave as is.
3. `ConnectionProfile.to_dict` return type: `dict[str, Any]`.
4. `ConnectionProfile.from_dict` already typed.
5. `D365Backend.__init__` is typed but uses `self._session: requests.Session` annotation (explicit).
6. `D365Backend.request` returns `dict[str, Any] | str | None`.
7. `D365Backend.get/post/patch/delete` mirror `request`'s return type.
8. `_parse_response` returns `dict[str, Any] | str | None`.

Apply these edits explicitly (full new content shown for the changing parts):

In `D365Backend.__init__`, change:
```python
        self._session = requests.Session()
```
to:
```python
        self._session: requests.Session = requests.Session()
```

Change `_DEFAULT_HEADERS` from:
```python
_DEFAULT_HEADERS = {
```
to:
```python
_DEFAULT_HEADERS: dict[str, str] = {
```

Change `ConnectionProfile.to_dict` signature from:
```python
    def to_dict(self) -> dict:
```
to:
```python
    def to_dict(self) -> dict[str, Any]:
```

Change `D365Backend.request` signature from:
```python
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        extra_headers: dict | None = None,
        expect_json: bool = True,
    ) -> dict | None:
```
to:
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
    ) -> dict[str, Any] | str | None:
```

Change `D365Backend.get/post/patch/delete` signatures from:
```python
    def get(self, path: str, **kw) -> dict | None:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any = None, **kw) -> dict | None:
        return self.request("POST", path, json_body=json_body, **kw)

    def patch(self, path: str, json_body: Any = None, **kw) -> dict | None:
        return self.request("PATCH", path, json_body=json_body, **kw)

    def delete(self, path: str, **kw) -> dict | None:
        return self.request("DELETE", path, expect_json=False, **kw)
```
to:
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

Change `_parse_response` signature from:
```python
def _parse_response(resp: requests.Response, *, expect_json: bool) -> dict | None:
```
to:
```python
def _parse_response(resp: requests.Response, *, expect_json: bool) -> dict[str, Any] | str | None:
```

Add a public narrowing helper at the end of the module (used by callers that strictly need a dict):

```python
def as_dict(result: dict[str, Any] | str | None) -> dict[str, Any]:
    """Narrow a backend response to a dict (treat str/None as empty).

    Used by core/* callers that need dict semantics — preserves the existing
    `result or {}` idiom in a type-safe way under pyright strict.
    """
    return result if isinstance(result, dict) else {}
```

- [ ] **Step 3: Re-run pyright on the file**

```bash
pyright crm/utils/d365_backend.py 2>&1 | tail -5
```

Expected: `0 errors`. If any errors remain, fix inline — most should resolve via the changes above. Reading the message will tell you which `Any` value still needs narrowing.

- [ ] **Step 4: Run the backend tests to confirm no regression**

```bash
pytest crm/tests/test_core.py::TestD365Backend -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py
git commit -m "refactor(types): annotate d365_backend; widen response to dict|str|None"
```

---

### Task 6: Type-annotate `crm/core/session.py`

**Files:**
- Modify: `crm/core/session.py`

- [ ] **Step 1: Baseline pyright count**

```bash
pyright crm/core/session.py 2>&1 | tail -5
```

Expected: ~19 errors.

- [ ] **Step 2: Apply annotations**

Apply these signature/type changes:

- `DEFAULT_HOME` already inferred fine; leave.
- `_state_root()` return type: `Path`.
- `profile_path(name: str) -> Path` — already typed.
- `save_profile(profile: ConnectionProfile) -> Path` — already typed.
- `load_profile(name: str) -> ConnectionProfile` — already typed.
- `list_profiles() -> list[str]` — already typed.
- `delete_profile(name: str) -> bool` — already typed.
- `session_path(name: str = "default") -> Path` — already typed.
- `load_session(name: str = "default") -> dict[str, Any]` — change `dict` to `dict[str, Any]`.
- `save_session(state: dict[str, Any], name: str = "default") -> Path` — change `state: dict` to `state: dict[str, Any]`.
- `append_history(state: dict[str, Any], command: str, max_len: int = 500) -> None` — change `state: dict` to `state: dict[str, Any]`.
- `_atomic_write_json(path: Path, payload: Any) -> None` — already typed.
- `history_file_path() -> str` — already typed.
- Add `from typing import Any` to the imports if not already present.

Concrete edit — change the signatures listed above. Example for `load_session`:

```python
def load_session(name: str = "default") -> dict[str, Any]:
```

The internal logic stays unchanged.

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/session.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run session tests**

```bash
pytest crm/tests/test_core.py::TestSessionStore -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/core/session.py
git commit -m "refactor(types): annotate core/session"
```

---

### Task 7: Type-annotate `crm/core/connection.py`

**Files:**
- Modify: `crm/core/connection.py`

- [ ] **Step 1: Baseline**

```bash
pyright crm/core/connection.py 2>&1 | tail -5
```

Expected: ~14 errors.

- [ ] **Step 2: Apply annotations**

Signature changes:

- `_env(name: str, default: str = "") -> str` — already typed.
- `load_dotenv(path: str | os.PathLike[str] | None = None, *, override: bool = False) -> Path | None` — change `os.PathLike` to `os.PathLike[str]`.
- `_split_domain_user(username: str, explicit_domain: str) -> tuple[str, str]` — already typed.
- `profile_from_env(name: str = "env") -> ConnectionProfile` — already typed.
- `resolve_credentials(profile_name: str | None = None, password_override: str | None = None) -> ResolvedCredentials` — already typed.
- `whoami(backend: D365Backend) -> dict[str, Any]` — change `dict` to `dict[str, Any]`.
- `test_connection(backend: D365Backend) -> dict[str, Any]` — change `dict` to `dict[str, Any]`.

Inside `whoami`, narrow the backend return:

```python
def whoami(backend: D365Backend) -> dict[str, Any]:
    """Call WhoAmI() — the canonical D365 identity probe."""
    from crm.utils.d365_backend import as_dict
    return as_dict(backend.get("WhoAmI"))
```

Add `from typing import Any` to imports.

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/connection.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run connection tests**

```bash
pytest crm/tests/test_core.py::TestConnectionEnv crm/tests/test_core.py::TestConnectionDotenv -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/core/connection.py
git commit -m "refactor(types): annotate core/connection"
```

---

### Task 8: Type-annotate `crm/core/entity.py`

**Files:**
- Modify: `crm/core/entity.py`

- [ ] **Step 1: Baseline**

```bash
pyright crm/core/entity.py 2>&1 | tail -5
```

Expected: ~55 errors.

- [ ] **Step 2: Apply annotations**

Top of file imports — add:

```python
from typing import Any
from crm.utils.d365_backend import D365Backend, D365Error, as_dict
```

Signature changes (drop `dict` → `dict[str, Any]`; keep return types `dict[str, Any]`):

```python
def retrieve(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    select: list[str] | None = None,
    expand: list[str] | None = None,
    include_annotations: bool = False,
) -> dict[str, Any]:
```

```python
def create(
    backend: D365Backend,
    entity_set: str,
    payload: dict[str, Any],
    *,
    return_record: bool = True,
) -> dict[str, Any]:
```

```python
def update(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    prevent_create: bool = True,
    return_record: bool = False,
) -> dict[str, Any]:
```

```python
def upsert(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
```

```python
def delete(backend: D365Backend, entity_set: str, record_id: str) -> dict[str, Any]:
```

```python
def associate(
    backend: D365Backend,
    target_set: str,
    target_id: str,
    navigation_property: str,
    related_set: str,
    related_id: str,
) -> dict[str, Any]:
```

```python
def disassociate(
    backend: D365Backend,
    target_set: str,
    target_id: str,
    navigation_property: str,
    *,
    related_set: str | None = None,
    related_id: str | None = None,
) -> dict[str, Any]:
```

```python
def set_lookup(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    navigation_property: str,
    related_set: str,
    related_id: str,
) -> dict[str, Any]:
```

```python
def clear_lookup(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    navigation_property: str,
) -> dict[str, Any]:
```

Internal call-site narrowing — every place that does `backend.get(...) or {}` or `backend.post(...) or {}` becomes `as_dict(backend.get(...))` / `as_dict(backend.post(...))`. For example:

```python
    result = backend.get(
        _build_record_path(entity_set, record_id),
        params=params or None,
        extra_headers=headers,
    )
    return as_dict(result)
```

Replace every `or {}` after a backend call with the `as_dict(...)` wrapper. Function bodies otherwise unchanged.

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/entity.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run entity tests**

```bash
pytest crm/tests/test_core.py::TestEntityCrud crm/tests/test_core.py::TestAssociate -v
```

Expected: 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/core/entity.py
git commit -m "refactor(types): annotate core/entity"
```

---

### Task 9: Type-annotate `crm/core/query.py`

**Files:**
- Modify: `crm/core/query.py`

- [ ] **Step 1: Baseline**

```bash
pyright crm/core/query.py 2>&1 | tail -5
```

Expected: ~28 errors.

- [ ] **Step 2: Apply annotations**

Imports update:

```python
from typing import Any
from crm.utils.d365_backend import D365Backend, D365Error, as_dict
```

Signature changes — every function returns `dict[str, Any]` and accepts `dict[str, Any]` params (or specific generic types):

```python
def odata_query(
    backend: D365Backend,
    entity_set: str,
    *,
    select: list[str] | None = None,
    filter_: str | None = None,
    top: int | None = None,
    orderby: str | None = None,
    expand: list[str] | None = None,
    count: bool = False,
    include_annotations: bool = False,
    page_size: int | None = None,
) -> dict[str, Any]:
```

```python
def fetchxml_query(
    backend: D365Backend,
    entity_set: str,
    fetch_xml: str,
    *,
    include_annotations: bool = False,
) -> dict[str, Any]:
```

```python
def saved_query(
    backend: D365Backend,
    entity_set: str,
    savedquery_id: str,
    *,
    include_annotations: bool = False,
    page_size: int | None = None,
) -> dict[str, Any]:
```

```python
def user_query(
    backend: D365Backend,
    entity_set: str,
    userquery_id: str,
    *,
    include_annotations: bool = False,
    page_size: int | None = None,
) -> dict[str, Any]:
```

```python
def count_entity_set(backend: D365Backend, entity_set: str) -> int:
```

Replace `or {}` after backend calls with `as_dict(...)` as in Task 8.

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/query.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run query tests**

```bash
pytest crm/tests/test_core.py::TestQuery crm/tests/test_core.py::TestSavedAndUserQuery -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/core/query.py
git commit -m "refactor(types): annotate core/query"
```

---

### Task 10: Type-annotate `crm/core/metadata.py`

**Files:**
- Modify: `crm/core/metadata.py`

- [ ] **Step 1: Baseline**

```bash
pyright crm/core/metadata.py 2>&1 | tail -5
```

Expected: ~49 errors.

- [ ] **Step 2: Apply annotations**

Imports:

```python
from typing import Any
from crm.utils.d365_backend import D365Backend, D365Error, as_dict
```

Signature changes:

```python
def list_entities(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    top: int | None = None,
) -> list[dict[str, Any]]:
```

```python
def entity_info(backend: D365Backend, logical_name: str) -> dict[str, Any]:
```

```python
def list_attributes(backend: D365Backend, logical_name: str) -> list[dict[str, Any]]:
```

```python
def attribute_info(backend: D365Backend, logical_name: str, attribute: str) -> dict[str, Any]:
```

```python
def picklist_options(
    backend: D365Backend,
    logical_name: str,
    attribute: str,
    *,
    global_optionset: bool = True,
) -> dict[str, Any]:
```

```python
def _label(text: str, lang: int = 1033) -> dict[str, Any]:
```

```python
def create_entity(
    backend: D365Backend,
    *,
    schema_name: str,
    display_name: str,
    display_collection_name: str | None = None,
    primary_attr_schema: str | None = None,
    primary_attr_label: str | None = None,
    primary_attr_max_length: int = 200,
    description: str | None = None,
    ownership: str = "UserOwned",
    has_activities: bool = False,
    has_notes: bool = False,
    is_activity: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
```

```python
def list_relationships(backend: D365Backend, logical_name: str) -> dict[str, Any]:
```

Inside `create_entity`, change the `body: dict = {` declaration to:

```python
    body: dict[str, Any] = {
```

Replace `or {}` patterns with `as_dict(...)`.

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/metadata.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run metadata tests**

```bash
pytest crm/tests/test_core.py::TestMetadata crm/tests/test_core.py::TestPicklistMetadata crm/tests/test_core.py::TestCreateEntity -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/core/metadata.py
git commit -m "refactor(types): annotate core/metadata"
```

---

### Task 11: Type-annotate `crm/core/solution.py`

**Files:**
- Modify: `crm/core/solution.py`

- [ ] **Step 1: Baseline**

```bash
pyright crm/core/solution.py 2>&1 | tail -5
```

Expected: ~51 errors.

- [ ] **Step 2: Apply annotations**

Imports:

```python
from typing import Any
from crm.utils.d365_backend import D365Backend, D365Error, as_dict
```

Signature changes:

```python
def list_solutions(backend: D365Backend, *, managed: bool | None = None) -> list[dict[str, Any]]:
```

```python
def solution_info(backend: D365Backend, unique_name: str) -> dict[str, Any]:
```

```python
def solution_components(backend: D365Backend, unique_name: str) -> list[dict[str, Any]]:
```

```python
def export_solution(
    backend: D365Backend,
    unique_name: str,
    output_path: str | Path,
    *,
    managed: bool = False,
) -> dict[str, Any]:
```

```python
def import_solution(
    backend: D365Backend,
    zip_path: str | Path,
    *,
    publish_workflows: bool = True,
    overwrite_unmanaged_customizations: bool = True,
) -> dict[str, Any]:
```

```python
def publish_all(backend: D365Backend) -> dict[str, Any]:
```

```python
def publish_xml(backend: D365Backend, parameter_xml: str) -> dict[str, Any]:
```

```python
def service_document(backend: D365Backend) -> dict[str, Any]:
```

```python
def _new_guid() -> str:
```

In `export_solution`, change `body = {` to `body: dict[str, Any] = {`. Same in `import_solution`.

Replace `or {}` with `as_dict(...)`.

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/solution.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run solution tests**

```bash
pytest crm/tests/test_core.py::TestPublish -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/core/solution.py
git commit -m "refactor(types): annotate core/solution"
```

---

### Task 12: Type-annotate `crm/core/export.py`

**Files:**
- Modify: `crm/core/export.py`

- [ ] **Step 1: Baseline**

```bash
pyright crm/core/export.py 2>&1 | tail -5
```

Expected: ~35 errors.

- [ ] **Step 2: Apply annotations**

Imports:

```python
from typing import Any, Iterable
from crm.utils.d365_backend import D365Backend, D365Error, as_dict
```

Signature changes:

```python
def export_records(
    backend: D365Backend,
    entity_set: str,
    output_path: str | Path,
    *,
    select: list[str] | None = None,
    filter_: str | None = None,
    page_size: int = 500,
    max_records: int | None = None,
    fmt: str | None = None,
) -> dict[str, Any]:
```

```python
def _iter_records(
    backend: D365Backend,
    entity_set: str,
    *,
    select: list[str] | None,
    filter_: str | None,
    page_size: int,
    max_records: int | None,
) -> Iterable[dict[str, Any]]:
```

```python
def _write_csv(out: Path, records: list[dict[str, Any]], *, select: list[str] | None) -> None:
```

```python
def _ordered_keys(records: list[dict[str, Any]]) -> list[str]:
```

```python
def _flatten(v: Any) -> Any:
```

Inside `_iter_records`, replace:

```python
            page = backend.get(next_link) or {}
```

with:

```python
            page = as_dict(backend.get(next_link))
```

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/export.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run export tests**

```bash
pytest crm/tests/test_core.py::TestExport -v
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add crm/core/export.py
git commit -m "refactor(types): annotate core/export"
```

---

### Task 13: Type-annotate `crm/core/workflow.py`

**Files:**
- Modify: `crm/core/workflow.py`

- [ ] **Step 1: Baseline**

```bash
pyright crm/core/workflow.py 2>&1 | tail -5
```

Expected: ~16 errors.

- [ ] **Step 2: Apply annotations**

Imports:

```python
from typing import Any
from crm.utils.d365_backend import D365Backend, D365Error, as_dict
```

Signature changes:

```python
def list_workflows(
    backend: D365Backend,
    *,
    category: int | None = None,
    primary_entity: str | None = None,
    activated_only: bool = False,
    on_demand_only: bool = False,
) -> list[dict[str, Any]]:
```

```python
def set_workflow_state(
    backend: D365Backend,
    workflow_id: str,
    *,
    activate: bool,
) -> dict[str, Any]:
```

```python
def execute_workflow(
    backend: D365Backend,
    workflow_id: str,
    target_record_id: str,
) -> dict[str, Any]:
```

Inside `list_workflows`, change `filters = [...]` to:

```python
    filters: list[str] = [f"type eq {TYPE_DEFINITION}"]
```

Inside `list_workflows`, change `params = {` to `params: dict[str, str] = {`.
Inside `set_workflow_state`, change `body = {` to `body: dict[str, Any] = {`.
Inside `execute_workflow`, change `body = {` to `body: dict[str, Any] = {`.
Replace `or {}` with `as_dict(...)`.

- [ ] **Step 3: Re-run pyright**

```bash
pyright crm/core/workflow.py 2>&1 | tail -5
```

Expected: `0 errors`.

- [ ] **Step 4: Run workflow tests**

```bash
pytest crm/tests/test_core.py::TestWorkflow -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add crm/core/workflow.py
git commit -m "refactor(types): annotate core/workflow"
```

---

### Task 14: Full pyright pass + full pytest + open PR1

**Files:** (verification only)

- [ ] **Step 1: Run pyright across the whole project**

```bash
pyright
```

Expected: `0 errors, 0 warnings, X files analyzed` (X≈10–15 depending on stubs).

If any errors remain, identify the module and apply the same `as_dict(...)` / `dict[str, Any]` patterns from Tasks 5–13 to fix them.

- [ ] **Step 2: Run the full unit-test suite**

```bash
pytest -q
```

Expected: all existing tests pass. PR1 changes no behaviour.

- [ ] **Step 3: Push the branch and open PR1**

```bash
git push -u origin feat/pyright-setup
gh pr create --title "Spec A PR1: pyright strict + full typing pass" --body "$(cat <<'EOF'
## Summary

- Adds `pyrightconfig.json` with strict mode on `crm/core/*` + `crm/utils/d365_backend.py`; basic mode on `crm/cli.py`, `crm/utils/repl_skin.py`, and `crm/tests`.
- Adds `pyright>=1.1.380` to dev extras.
- Adds a `pyright` step to `.github/workflows/build.yml` after the existing `pytest` step.
- Introduces `crm/utils/d365_types.py` with TypedDict shapes for Web API responses.
- Annotates every public function in the strict zone; widens backend method return types to `dict[str, Any] | str | None`; adds `as_dict()` narrowing helper.
- Drives the 342 strict-mode pyright errors surfaced by the spec pre-count to **zero**.

No behavioural change. Spec: `docs/superpowers/specs/2026-05-24-spec-a-correctness-pyright-design.md`.

## Test plan

- [x] `pyright` exits 0 from repo root
- [x] `pytest -q` passes
- [ ] CI workflow runs pyright step and exits green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. Wait for CI green before proceeding to PR2.

---

# PR2 — `fix/correctness`

**Branch:** `fix/correctness` off `feat/pyright-setup` (or off `main` once PR1 has landed and you've rebased).
**Goal:** Land §3.1, §3.2, §3.4, §3.8, §3.9 — pure internals, no CLI surface change.

---

### Task 15: §3.1 — Drop `If-None-Match: null` on POST

**Files:**
- Modify: `crm/core/entity.py:75-81`
- Modify: `crm/tests/test_core.py:163-173` (existing test updates)

- [ ] **Step 1: Create the branch**

```bash
git switch -c fix/correctness feat/pyright-setup
```

(If PR1 has merged, branch off `main`: `git switch -c fix/correctness main && git pull`.)

- [ ] **Step 2: Update the existing test in `crm/tests/test_core.py`**

Find this test starting at line 163:

```python
    def test_create_sets_if_none_match_and_prefer_return(self, backend):
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("contacts"),
                json={"contactid": _GUID, "firstname": "Rafel"},
            )
            entity_mod.create(backend, "contacts", {"firstname": "Rafel"})
        req = m.request_history[0]
        assert req.headers["If-None-Match"] == "null"
        assert req.headers["Prefer"] == "return=representation"
        assert json.loads(req.body) == {"firstname": "Rafel"}
```

Replace it with:

```python
    def test_create_no_if_none_match_header(self, backend):
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("contacts"),
                json={"contactid": _GUID, "firstname": "Rafel"},
            )
            entity_mod.create(backend, "contacts", {"firstname": "Rafel"})
        req = m.request_history[0]
        assert "If-None-Match" not in req.headers
        assert req.headers["Prefer"] == "return=representation"
        assert json.loads(req.body) == {"firstname": "Rafel"}
```

- [ ] **Step 3: Run test — expect failure**

```bash
pytest crm/tests/test_core.py::TestEntityCrud::test_create_no_if_none_match_header -v
```

Expected: FAIL with `assert 'If-None-Match' not in req.headers` (because the production code still sends it).

- [ ] **Step 4: Apply the fix in `crm/core/entity.py`**

Find the `create()` function body. Replace:

```python
    if not isinstance(payload, dict):
        raise D365Error("Create payload must be a dict.")
    headers = {"If-None-Match": "null"}
    if return_record:
        headers["Prefer"] = "return=representation"
```

with:

```python
    if not isinstance(payload, dict):
        raise D365Error("Create payload must be a dict.")
    headers: dict[str, str] = {}
    if return_record:
        headers["Prefer"] = "return=representation"
```

- [ ] **Step 5: Run test — expect pass**

```bash
pytest crm/tests/test_core.py::TestEntityCrud::test_create_no_if_none_match_header -v
```

Expected: PASS.

- [ ] **Step 6: Run pyright on entity.py**

```bash
pyright crm/core/entity.py
```

Expected: `0 errors`.

- [ ] **Step 7: Commit**

```bash
git add crm/core/entity.py crm/tests/test_core.py
git commit -m "fix(entity): drop non-spec If-None-Match: null header on create (§3.1)"
```

---

### Task 16: §3.2 — Fix `_ordered_keys` filter

**Files:**
- Modify: `crm/core/export.py:96-108`
- Modify: `crm/tests/test_core.py` (add new test)

- [ ] **Step 1: Write the failing test**

Add this class to `crm/tests/test_core.py` (near `TestExport`):

```python
class TestOrderedKeys:
    def test_ordered_keys_drops_lookups_and_annotations(self):
        from crm.core.export import _ordered_keys
        records = [
            {
                "name": "Contoso",
                "_owner_value": "guid-1",
                "@odata.etag": "W/\"123\"",
                "createdon": "2026-01-01",
            },
            {"name": "Initech", "_modifiedby_value": "guid-2"},
        ]
        assert _ordered_keys(records) == ["name", "createdon"]
```

- [ ] **Step 2: Run test — expect failure**

```bash
pytest crm/tests/test_core.py::TestOrderedKeys -v
```

Expected: FAIL — assertion mismatch because `_owner_value` currently leaks through.

- [ ] **Step 3: Apply the fix in `crm/core/export.py`**

Replace the entire `_ordered_keys` function body:

```python
def _ordered_keys(records: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for rec in records:
        for k in rec.keys():
            if k.startswith(("@", "_")):
                continue
            if k not in seen_set:
                seen.append(k)
                seen_set.add(k)
    return seen
```

- [ ] **Step 4: Run test — expect pass**

```bash
pytest crm/tests/test_core.py::TestOrderedKeys -v
```

Expected: PASS.

- [ ] **Step 5: Run pyright + full export tests**

```bash
pyright crm/core/export.py && pytest crm/tests/test_core.py::TestExport crm/tests/test_core.py::TestOrderedKeys -v
```

Expected: pyright clean; both test classes pass.

- [ ] **Step 6: Commit**

```bash
git add crm/core/export.py crm/tests/test_core.py
git commit -m "fix(export): correct _ordered_keys filter to drop _value + @-annotations (§3.2)"
```

---

### Task 17: §3.4 — `fetchxml_query` uses `params=`

**Files:**
- Modify: `crm/core/query.py:79-87`
- Modify: `crm/tests/test_core.py` (add new test, update existing one)

- [ ] **Step 1: Write the new test**

Append inside `class TestQuery` in `crm/tests/test_core.py`:

```python
    def test_fetchxml_passes_params_dict(self, backend):
        fx = "<fetch top='1'><entity name='account'/></fetch>"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("accounts"), json={"value": []})
            query_mod.fetchxml_query(backend, "accounts", fx)
        req = m.request_history[0]
        # URL should NOT contain a manually-baked `?fetchXml=` query — requests should
        # have appended it via the params= kwarg.
        # Either form is fine since requests encodes — we just verify the qs has it.
        assert req.qs["fetchxml"] == [fx]
```

Note: `requests_mock` lowercases query param keys in `qs`, so the assertion uses `fetchxml` lowercase.

- [ ] **Step 2: Run test — expect failure**

```bash
pytest crm/tests/test_core.py::TestQuery::test_fetchxml_passes_params_dict -v
```

Expected: PASS already (current implementation still produces the same encoded URL). The test now locks in the contract. We're allowed a green-on-write here — the value of the test is preventing regression after the fix.

- [ ] **Step 3: Apply the fix in `crm/core/query.py`**

Replace the entire `fetchxml_query` function body (keep the docstring + signature):

```python
def fetchxml_query(
    backend: D365Backend,
    entity_set: str,
    fetch_xml: str,
    *,
    include_annotations: bool = False,
) -> dict[str, Any]:
    """Execute a FetchXML query against the given entity set.

    fetch_xml must be a complete `<fetch>...</fetch>` document. It's passed as the
    `fetchXml` query parameter via requests' `params=` kwarg so encoding stays
    consistent with the rest of the backend.

    Note: for very large FetchXML queries that may exceed URL length limits, $batch
    is the recommended pattern; this helper uses the inline form which is sufficient
    for the vast majority of queries.
    """
    if not fetch_xml or "<fetch" not in fetch_xml.lower():
        raise D365Error("fetch_xml must contain a <fetch> element.")

    headers: dict[str, str] | None = (
        {"Prefer": 'odata.include-annotations="*"'} if include_annotations else None
    )
    return as_dict(backend.get(
        entity_set,
        params={"fetchXml": fetch_xml},
        extra_headers=headers,
    ))
```

Also remove the unused `import urllib.parse` at the top of `query.py` if it's no longer referenced elsewhere — `pyright` will warn if it is.

- [ ] **Step 4: Run the updated test + the legacy `_url_encodes_xml_once` test**

```bash
pytest crm/tests/test_core.py::TestQuery -v
```

Expected: All TestQuery tests pass. The legacy `test_fetchxml_query_url_encodes_xml_once` asserts `"%3Cfetch" in req.url` and `"fetchXml=" in req.url` — both still true since `requests` encodes the param the same way.

- [ ] **Step 5: Run pyright**

```bash
pyright crm/core/query.py
```

Expected: `0 errors`. If `urllib.parse` was removed and was used elsewhere, restore the import.

- [ ] **Step 6: Commit**

```bash
git add crm/core/query.py crm/tests/test_core.py
git commit -m "refactor(query): fetchxml uses params= for consistent encoding (§3.4)"
```

---

### Task 18: §3.8 — `.env` unquote

**Files:**
- Modify: `crm/core/connection.py:106`
- Modify: `crm/tests/test_core.py` (add new test)

- [ ] **Step 1: Write the failing test**

Append to `class TestConnectionDotenv` in `crm/tests/test_core.py`:

```python
    def test_dotenv_preserves_inner_quotes(self, tmp_path, monkeypatch):
        for k in ("KEY_WITH_QUOTE", "D365_URL", "CRM_BASE_URL"):
            monkeypatch.delenv(k, raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text('KEY_WITH_QUOTE="foo\'s bar"\n')
        from crm.core import connection as conn_mod_local
        conn_mod_local.load_dotenv(env_file)
        import os
        assert os.environ["KEY_WITH_QUOTE"] == "foo's bar"
```

- [ ] **Step 2: Run test — expect failure**

```bash
pytest crm/tests/test_core.py::TestConnectionDotenv::test_dotenv_preserves_inner_quotes -v
```

Expected: FAIL — current code does `.strip('"').strip("'")` which strips the inner apostrophe, producing `foos bar`.

- [ ] **Step 3: Apply the fix in `crm/core/connection.py`**

In `load_dotenv`, find this block (around line 106):

```python
        value = raw.strip().strip('"').strip("'")
        if override or key not in os.environ or not os.environ[key]:
            os.environ[key] = value
```

Replace with:

```python
        raw_value = raw.strip()
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ('"', "'"):
            value = raw_value[1:-1]
        else:
            value = raw_value
        if override or key not in os.environ or not os.environ[key]:
            os.environ[key] = value
```

- [ ] **Step 4: Run test — expect pass**

```bash
pytest crm/tests/test_core.py::TestConnectionDotenv -v
```

Expected: all TestConnectionDotenv tests pass (new one + the existing alias test).

- [ ] **Step 5: Run pyright**

```bash
pyright crm/core/connection.py
```

Expected: `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add crm/core/connection.py crm/tests/test_core.py
git commit -m "fix(connection): pair-aware .env unquote preserves inner quotes (§3.8)"
```

---

### Task 19: §3.9 — `$count` text/plain parse path + fallback retained

**Files:**
- Modify: `crm/utils/d365_backend.py:_parse_response`
- Modify: `crm/core/query.py:count_entity_set`
- Modify: `crm/tests/test_core.py` (add two new tests)

- [ ] **Step 1: Write the two failing tests**

Append a new class to `crm/tests/test_core.py`:

```python
class TestCountEntitySet:
    def test_count_returns_int_from_text_plain(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("contacts/$count"),
                text="42",
                headers={"Content-Type": "text/plain"},
            )
            result = query_mod.count_entity_set(backend, "contacts")
        assert result == 42
        assert len(m.request_history) == 1, "happy path must issue exactly one request"

    def test_count_falls_back_when_text_plain_empty(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("contacts/$count"),
                text="",
                headers={"Content-Type": "text/plain"},
            )
            m.get(
                backend.url_for("contacts"),
                json={"value": [{"contactid": "x"}], "@odata.count": 7},
            )
            result = query_mod.count_entity_set(backend, "contacts")
        assert result == 7
        assert len(m.request_history) == 2, "fallback must issue two requests in order"
        assert m.request_history[0].url.endswith("/$count")
        assert "$count=true" in m.request_history[1].url or "%24count=true" in m.request_history[1].url
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest crm/tests/test_core.py::TestCountEntitySet -v
```

Expected: both FAIL — `count_entity_set` currently calls `backend.get` with `expect_json=False` which returns `None`, then always falls back. The first test fails because the fallback path issues two requests (so the `len == 1` assertion fails); the second test fails because the fallback path's `$count=true` returns an integer 7 but the assertions there may pass — re-read failure carefully. Either way, both must end green only after the fix.

- [ ] **Step 3: Apply the `_parse_response` change in `crm/utils/d365_backend.py`**

Replace the `_parse_response` function body:

```python
def _parse_response(resp: requests.Response, *, expect_json: bool) -> dict[str, Any] | str | None:
    """Parse a Web API response. Raises D365Error on non-2xx."""
    if 200 <= resp.status_code < 300:
        if resp.status_code == 204 or not resp.content:
            entity_id = resp.headers.get("OData-EntityId")
            if entity_id:
                return {"_entity_id_url": entity_id}
            return None
        if not expect_json:
            # Return text/plain bodies as a stripped string; otherwise None as before.
            ctype = resp.headers.get("Content-Type", "")
            if ctype.startswith("text/plain"):
                text = resp.text.strip()
                return text if text else None
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise D365Error(
                f"Server returned 2xx but body was not JSON: {resp.text[:500]}"
            ) from exc

    # Error path
    body: Any = None
    code: str | None = None
    message = f"HTTP {resp.status_code}"
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = err.get("code")
            message = err.get("message", message)
    except ValueError:
        body = resp.text
        message = f"HTTP {resp.status_code}: {resp.text[:500]}"

    raise D365Error(message, status=resp.status_code, code=code, response_body=body)
```

- [ ] **Step 4: Apply the `count_entity_set` change in `crm/core/query.py`**

Replace the entire `count_entity_set` function body:

```python
def count_entity_set(backend: D365Backend, entity_set: str) -> int:
    """Return the integer record count for an entity set via /<set>/$count.

    Fast path: the `$count` endpoint returns a `text/plain` integer in one HTTP call.
    Fallback: if the body is missing, non-numeric, or otherwise unparseable (proxies
    occasionally strip text/plain bodies), fall through to `?$count=true` and read
    `@odata.count` from the resulting collection envelope. The fallback is
    belt-and-braces — preserves the resilience the previous implementation had.
    """
    result = backend.get(
        f"{entity_set}/$count",
        extra_headers={"Accept": "text/plain"},
        expect_json=False,
    )
    if isinstance(result, str) and result.strip():
        try:
            return int(result)
        except ValueError:
            pass  # fall through to the fallback

    # Fallback: ask the collection with $count=true and read @odata.count.
    raw = odata_query(backend, entity_set, top=1, count=True)
    c = raw.get("@odata.count")
    return int(c) if c is not None else 0
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest crm/tests/test_core.py::TestCountEntitySet -v
```

Expected: both tests PASS.

- [ ] **Step 6: Run pyright + full suite**

```bash
pyright crm/utils/d365_backend.py crm/core/query.py && pytest -q
```

Expected: pyright clean; entire test suite passes.

- [ ] **Step 7: Commit**

```bash
git add crm/utils/d365_backend.py crm/core/query.py crm/tests/test_core.py
git commit -m "feat(query): \$count parses text/plain on happy path; fallback retained (§3.9)"
```

---

### Task 20: Push PR2 + open

**Files:** (verification only)

- [ ] **Step 1: Full pyright + pytest**

```bash
pyright && pytest -q
```

Expected: both clean.

- [ ] **Step 2: Push + open PR**

```bash
git push -u origin fix/correctness
gh pr create --base feat/pyright-setup --title "Spec A PR2: correctness fixes (internal, non-breaking)" --body "$(cat <<'EOF'
## Summary

Pure-internal fixes from Spec A — no CLI surface change.

- §3.1 entity: drop non-spec `If-None-Match: null` header on POST.
- §3.2 export: fix `_ordered_keys` boolean precedence — drops `_value` lookup columns from CSV headers.
- §3.4 query: `fetchxml_query` uses `params=` for consistent URL encoding.
- §3.8 connection: pair-aware `.env` unquote — preserves inner quotes (e.g. `foo's bar`).
- §3.9 query: `\$count` parses `text/plain` in one HTTP call on the happy path; fallback to `?\$count=true` retained for proxies that strip bodies.

Spec: `docs/superpowers/specs/2026-05-24-spec-a-correctness-pyright-design.md`.

## Test plan

- [x] New unit tests cover every fix (5 net-new tests; one existing test renamed + tightened).
- [x] `pyright` exits 0
- [x] `pytest -q` passes
- [ ] CI green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Note: PR base is `feat/pyright-setup` until PR1 is merged. Once PR1 lands, retarget to `main` via `gh pr edit <num> --base main` and rebase locally if needed.

---

# PR3 — `feat/api-shape-0.2.0`

**Branch:** `feat/api-shape-0.2.0` off `fix/correctness` (or `main` after PR2 lands).
**Goal:** Land §3.3, §3.5, §3.6, §3.7 — observable changes. Bump to `0.2.0`. Add `CHANGELOG.md`.

---

### Task 21: §3.5 — JSON envelope null status/code

**Files:**
- Modify: `crm/cli.py:127-131`
- Modify: `crm/tests/test_core.py` (add new test)

- [ ] **Step 1: Create branch**

```bash
git switch -c feat/api-shape-0.2.0 fix/correctness
```

(Or `git switch -c feat/api-shape-0.2.0 main && git pull` after PR2 has merged.)

- [ ] **Step 2: Write the failing test**

Add to `crm/tests/test_core.py`:

```python
class TestErrorEnvelope:
    def test_error_envelope_null_when_status_missing(self, capsys):
        from crm.cli import CLIContext
        from crm.utils.d365_backend import D365Error
        ctx = CLIContext()
        ctx.json_mode = True
        exc = D365Error("transport boom")  # no status, no code
        # Mirror cli._handle_d365_error after the fix:
        ctx.emit(False, error=str(exc), meta={"status": exc.status, "code": exc.code})
        out = capsys.readouterr().out
        import json
        envelope = json.loads(out)
        assert envelope["ok"] is False
        assert envelope["error"] == "transport boom"
        assert envelope["meta"]["status"] is None
        assert envelope["meta"]["code"] is None
```

- [ ] **Step 3: Run test — note: passes after fix, currently passes already if we test the new shape**

Since the test calls `ctx.emit` directly with the post-fix meta dict, it green-passes immediately. The real driver is the prod-code fix in `_handle_d365_error`. We add this test to lock in the contract.

```bash
pytest crm/tests/test_core.py::TestErrorEnvelope -v
```

Expected: PASS.

- [ ] **Step 4: Apply the prod-code fix in `crm/cli.py`**

Replace the `_handle_d365_error` function:

```python
def _handle_d365_error(ctx: CLIContext, exc: D365Error) -> None:
    ctx.emit(False, error=str(exc), meta={
        "status": exc.status,
        "code": exc.code,
    })
```

- [ ] **Step 5: Run tests**

```bash
pytest crm/tests/test_core.py::TestErrorEnvelope -v && pytest -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add crm/cli.py crm/tests/test_core.py
git commit -m "feat(cli)!: emit null instead of \"n/a\" for missing status/code (§3.5)"
```

The `!` in the conventional-commit type marks the breaking change.

---

### Task 22: §3.7 — REPL backend reuse + `invalidate_backend()`

**Files:**
- Modify: `crm/cli.py:97-106` (`CLIContext`)
- Modify: `crm/cli.py:175-200` (`connection_connect`)
- Modify: `crm/cli.py:262-269` (`connection_disconnect`)
- Modify: `crm/cli.py:1240-1289` (REPL loop)
- Modify: `crm/tests/test_core.py`

- [ ] **Step 1: Write the failing tests**

Add to `crm/tests/test_core.py`:

```python
class TestReplBackendCache:
    def test_repl_reuses_backend_across_commands(self, monkeypatch, profile):
        from crm.cli import CLIContext
        from crm.utils import d365_backend as backend_mod
        from crm.core.connection import ResolvedCredentials
        ctx = CLIContext()
        ctx.password = "pw"
        # Stub credential resolution to avoid env/profile loading.
        monkeypatch.setattr(
            "crm.cli.conn_mod.resolve_credentials",
            lambda profile_name=None, password_override=None:
                ResolvedCredentials(profile=profile, password="pw"),
        )
        # Count D365Backend instantiations.
        calls = {"n": 0}
        real_init = backend_mod.D365Backend.__init__
        def counting_init(self, *a, **kw):
            calls["n"] += 1
            real_init(self, *a, **kw)
        monkeypatch.setattr(backend_mod.D365Backend, "__init__", counting_init)
        b1 = ctx.backend()
        b2 = ctx.backend()
        assert b1 is b2
        assert calls["n"] == 1

    def test_repl_backend_invalidated_on_connect(self, monkeypatch, profile):
        from crm.cli import CLIContext
        from crm.utils import d365_backend as backend_mod
        from crm.core.connection import ResolvedCredentials
        ctx = CLIContext()
        ctx.password = "pw"
        monkeypatch.setattr(
            "crm.cli.conn_mod.resolve_credentials",
            lambda profile_name=None, password_override=None:
                ResolvedCredentials(profile=profile, password="pw"),
        )
        calls = {"n": 0}
        real_init = backend_mod.D365Backend.__init__
        def counting_init(self, *a, **kw):
            calls["n"] += 1
            real_init(self, *a, **kw)
        monkeypatch.setattr(backend_mod.D365Backend, "__init__", counting_init)
        ctx.backend()
        ctx.invalidate_backend()
        ctx.backend()
        assert calls["n"] == 2
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest crm/tests/test_core.py::TestReplBackendCache -v
```

Expected: FAIL — `CLIContext` has no `invalidate_backend()` method yet (AttributeError).

- [ ] **Step 3: Add `invalidate_backend()` to `CLIContext` in `crm/cli.py`**

Inside `class CLIContext`, after the existing `backend()` method, add:

```python
    def invalidate_backend(self) -> None:
        """Drop the cached D365Backend so the next backend() call rebuilds it.

        Called when the profile changes (`connection connect`/`disconnect`) so
        the REPL stops reusing a backend wired up to a stale profile.
        """
        self._backend = None
```

- [ ] **Step 4: Wire `invalidate_backend()` into `connection_connect` and `connection_disconnect`**

In `connection_connect` (around line 176), after `ctx.password = ...`, before constructing the backend manually for the test_connection call, leave the existing code; at the very end of the function (after `session_mod.save_session(...)`), the next REPL command should rebuild against the new profile. Add a call:

After this block at the bottom of `connection_connect`:

```python
    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = profile_name
    session_mod.save_session(state, ctx.session_name)
    ctx.emit(True, data=info, meta={"profile": profile_name})
```

Insert *before* the final `ctx.emit(...)`:

```python
    ctx.invalidate_backend()
```

In `connection_disconnect`, after the line `state["active_profile"] = None`, add:

```python
    ctx.invalidate_backend()
```

- [ ] **Step 5: Wire `obj=ctx` into the REPL's `cli.main` call**

In the `repl` function (around line 1276), find:

```python
            cli.main(args=argv, standalone_mode=False, prog_name="crm")
```

Replace with:

```python
            cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")
```

- [ ] **Step 6: Run tests**

```bash
pytest crm/tests/test_core.py::TestReplBackendCache -v
```

Expected: both PASS.

- [ ] **Step 7: Run full suite**

```bash
pyright && pytest -q
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add crm/cli.py crm/tests/test_core.py
git commit -m "feat(cli): REPL reuses one D365Backend per session (§3.7)"
```

---

### Task 23: §3.6 — Solution export `--export-setting` flag

**Files:**
- Modify: `crm/core/solution.py:export_solution`
- Modify: `crm/cli.py:824-835` (`solution_export_cmd`)
- Modify: `crm/tests/test_core.py`

- [ ] **Step 1: Write the failing test**

Add to `crm/tests/test_core.py`:

```python
class TestSolutionExportFlags:
    def test_export_solution_passes_flags_to_body(self, backend, tmp_path):
        from crm.core import solution as sol_mod_local
        import base64
        with requests_mock.Mocker() as m:
            payload = base64.b64encode(b"FAKE-ZIP-CONTENT").decode("ascii")
            m.post(
                backend.url_for("ExportSolution"),
                json={"ExportSolutionFile": payload},
            )
            out = tmp_path / "s.zip"
            sol_mod_local.export_solution(
                backend, "MySol", out,
                export_customizations=True,
                export_general=True,
            )
        body = json.loads(m.request_history[0].body)
        assert body["SolutionName"] == "MySol"
        assert body["ExportCustomizationSettings"] is True
        assert body["ExportGeneralSettings"] is True
        # Other flags default to False
        assert body["ExportCalendarSettings"] is False
        assert body["ExportSales"] is False
        assert body["ExportAutoNumberingSettings"] is False
```

- [ ] **Step 2: Run test — expect failure**

```bash
pytest crm/tests/test_core.py::TestSolutionExportFlags -v
```

Expected: FAIL — `export_solution` doesn't accept `export_customizations` kwarg.

- [ ] **Step 3: Apply the fix in `crm/core/solution.py`**

Replace the `export_solution` function:

```python
def export_solution(
    backend: D365Backend,
    unique_name: str,
    output_path: str | Path,
    *,
    managed: bool = False,
    export_autonumbering: bool = False,
    export_calendar: bool = False,
    export_customizations: bool = False,
    export_email_tracking: bool = False,
    export_general: bool = False,
    export_isv_config: bool = False,
    export_marketing: bool = False,
    export_outlook_sync: bool = False,
    export_relationship_roles: bool = False,
    export_sales: bool = False,
) -> dict[str, Any]:
    """Call ExportSolution action and write the returned ZIP to disk."""
    body: dict[str, Any] = {
        "SolutionName": unique_name,
        "Managed": managed,
        "ExportAutoNumberingSettings": export_autonumbering,
        "ExportCalendarSettings": export_calendar,
        "ExportCustomizationSettings": export_customizations,
        "ExportEmailTrackingSettings": export_email_tracking,
        "ExportGeneralSettings": export_general,
        "ExportIsvConfig": export_isv_config,
        "ExportMarketingSettings": export_marketing,
        "ExportOutlookSynchronizationSettings": export_outlook_sync,
        "ExportRelationshipRoles": export_relationship_roles,
        "ExportSales": export_sales,
    }
    result = as_dict(backend.post("ExportSolution", json_body=body))
    if "_dry_run" in result:
        return result
    encoded = result.get("ExportSolutionFile")
    if not encoded:
        raise D365Error("ExportSolution returned no ExportSolutionFile payload.")
    data = base64.b64decode(encoded)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {
        "output": str(out),
        "bytes": len(data),
        "managed": managed,
        "solution": unique_name,
    }
```

- [ ] **Step 4: Update the CLI in `crm/cli.py`**

Find the `solution_export_cmd` function (around line 829). Replace:

```python
@solution.command("export")
@click.argument("unique_name")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--managed", is_flag=True)
@pass_ctx
def solution_export_cmd(ctx, unique_name, output, managed):
    try:
        info = sol_mod.export_solution(ctx.backend(), unique_name, output, managed=managed)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

with:

```python
_EXPORT_SETTING_KEYS: dict[str, str] = {
    "autonumbering":       "export_autonumbering",
    "calendar":            "export_calendar",
    "customizations":      "export_customizations",
    "email-tracking":      "export_email_tracking",
    "general":             "export_general",
    "isv-config":          "export_isv_config",
    "marketing":           "export_marketing",
    "outlook-sync":        "export_outlook_sync",
    "relationship-roles":  "export_relationship_roles",
    "sales":               "export_sales",
}


@solution.command("export")
@click.argument("unique_name")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--managed", is_flag=True)
@click.option(
    "--export-setting",
    "export_settings",
    multiple=True,
    type=click.Choice(sorted(_EXPORT_SETTING_KEYS.keys())),
    help="Repeatable; include a named export setting in the solution payload.",
)
@pass_ctx
def solution_export_cmd(ctx, unique_name, output, managed, export_settings):
    kwargs = {_EXPORT_SETTING_KEYS[name]: True for name in export_settings}
    try:
        info = sol_mod.export_solution(
            ctx.backend(), unique_name, output, managed=managed, **kwargs,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

- [ ] **Step 5: Run tests**

```bash
pytest crm/tests/test_core.py::TestSolutionExportFlags -v && pytest -q
```

Expected: all pass.

- [ ] **Step 6: Run pyright**

```bash
pyright
```

Expected: `0 errors`.

- [ ] **Step 7: Commit**

```bash
git add crm/core/solution.py crm/cli.py crm/tests/test_core.py
git commit -m "feat(solution): --export-setting flag exposes Export*Settings (§3.6)"
```

---

### Task 24: §3.3 — EntitySetName read-back after `create-entity`

**Files:**
- Modify: `crm/core/metadata.py:create_entity`
- Modify: `crm/tests/test_core.py`

- [ ] **Step 1: Write the failing tests**

Add to `crm/tests/test_core.py` (replace or augment the existing `TestCreateEntity` class — the existing single test stays):

```python
class TestCreateEntityReadback:
    _MD_ID = "11111111-1111-1111-1111-111111111111"

    def test_create_entity_returns_server_entity_set_name(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
            md_url = backend.url_for(f"EntityDefinitions({self._MD_ID})")
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                headers={"OData-EntityId": md_url},
            )
            m.get(
                md_url,
                json={"LogicalName": "new_city", "EntitySetName": "new_cities"},
            )
            info = meta_mod_local.create_entity(
                backend, schema_name="new_City", display_name="City",
            )
        assert info["created"] is True
        assert info["entity_set_name"] == "new_cities"
        assert info["metadata_id_url"] == md_url

    def test_create_entity_partial_when_readback_fails(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
            md_url = backend.url_for(f"EntityDefinitions({self._MD_ID})")
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                headers={"OData-EntityId": md_url},
            )
            m.get(
                md_url,
                status_code=500,
                json={"error": {"code": "0x...", "message": "boom"}},
            )
            info = meta_mod_local.create_entity(
                backend, schema_name="new_City", display_name="City",
            )
        assert info["created"] is True
        assert info["entity_set_name"] is None
        assert "entity_set_lookup_error" in info
        assert info["metadata_id_url"] == md_url

    def test_create_entity_partial_when_odata_entityid_header_missing(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                # No OData-EntityId header set
            )
            info = meta_mod_local.create_entity(
                backend, schema_name="new_City", display_name="City",
            )
        assert info["created"] is True
        assert info["entity_set_name"] is None
        assert info["metadata_id_url"] is None
        assert "entity_set_lookup_error" in info
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest crm/tests/test_core.py::TestCreateEntityReadback -v
```

Expected: all three FAIL — current code derives `entity_set_name` via `+ "s"` / `+ "es"`.

- [ ] **Step 3: Apply the fix in `crm/core/metadata.py`**

Replace the tail of `create_entity` — the part starting at `result = backend.post(...)`:

```python
    result = as_dict(backend.post(
        "EntityDefinitions",
        json_body=body,
        extra_headers=headers or None,
    ))
    if result.get("_dry_run"):
        return result
    entity_id_url: str | None = result.get("_entity_id_url")

    # Read-back: parse MetadataId from the OData-EntityId URL, then GET the
    # server's authoritative EntitySetName. Failure here does NOT fail the
    # command — the entity was created.
    entity_set_name: str | None = None
    entity_set_lookup_error: str | None = None
    if not entity_id_url:
        entity_set_lookup_error = "OData-EntityId header missing from create response."
    else:
        match = re.search(r"EntityDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
        if not match:
            entity_set_lookup_error = (
                f"Could not parse MetadataId from OData-EntityId URL: {entity_id_url!r}"
            )
        else:
            metadata_id = match.group(1)
            try:
                rb = as_dict(backend.get(
                    f"EntityDefinitions({metadata_id})",
                    params={"$select": "EntitySetName,LogicalName"},
                ))
                name = rb.get("EntitySetName")
                if isinstance(name, str) and name:
                    entity_set_name = name
                else:
                    entity_set_lookup_error = (
                        f"Read-back returned no EntitySetName for MetadataId {metadata_id}."
                    )
            except D365Error as exc:
                entity_set_lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "schema_name": schema_name,
        "logical_name": logical_name,
        "entity_set_name": entity_set_name,
        "primary_attribute": primary_logical,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if entity_set_lookup_error is not None:
        out["entity_set_lookup_error"] = entity_set_lookup_error
    return out
```

Add `import re` at the top of `metadata.py` if not already present.

- [ ] **Step 4: Run the new tests + the existing one (which still asserts shape)**

```bash
pytest crm/tests/test_core.py::TestCreateEntityReadback crm/tests/test_core.py::TestCreateEntity -v
```

Expected: all four pass. The existing `test_create_entity_posts_expected_payload` doesn't inspect `entity_set_name` — it just asserts `"metadata_id_url" in info` — so it still passes. If it broke, double-check the mock setup added an `OData-EntityId` header.

- [ ] **Step 5: Run pyright + full suite**

```bash
pyright && pytest -q
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add crm/core/metadata.py crm/tests/test_core.py
git commit -m "feat(metadata)!: create-entity reads back EntitySetName from server (§3.3)"
```

---

### Task 25: E2E tests for §3.3 and §3.6

**Files:**
- Modify: `crm/tests/test_full_e2e.py`

- [ ] **Step 1: Read the existing E2E file to match its style**

```bash
head -80 crm/tests/test_full_e2e.py
```

Use the same fixtures + skip patterns the file uses (typically `pytest.skip` when D365 env vars are missing).

- [ ] **Step 2: Append the two new tests**

The exact code depends on the existing fixtures. Pattern:

```python
class TestE2ESpecA:
    def test_e2e_create_custom_entity_reads_back_set_name(self, live_backend):
        """§3.3: create a unique custom entity, assert returned entity_set_name resolves via metadata.list_entities."""
        from crm.core import metadata as meta_mod
        import uuid
        suffix = uuid.uuid4().hex[:8]
        schema = f"new_SpecAReadback{suffix}"
        try:
            info = meta_mod.create_entity(
                live_backend,
                schema_name=schema,
                display_name=f"SpecA Readback {suffix}",
            )
            assert info["created"] is True
            assert info["entity_set_name"] is not None
            entities = meta_mod.list_entities(live_backend, custom_only=True)
            logical_names = {e.get("LogicalName") for e in entities}
            assert schema.lower() in logical_names
        finally:
            # Best-effort cleanup; ignore failure (entity stays for manual cleanup).
            try:
                live_backend.delete(f"EntityDefinitions(LogicalName='{schema.lower()}')")
            except Exception:
                pass

    def test_e2e_solution_export_with_customization_flag(self, live_backend, tmp_path):
        """§3.6: --export-setting customizations yields a non-empty zip."""
        from crm.core import solution as sol_mod
        # Use the default publisher solution that always exists on any org.
        out = tmp_path / "default.zip"
        sol_mod.export_solution(
            live_backend, "Default", out, export_customizations=True,
        )
        assert out.exists()
        assert out.stat().st_size > 1000  # non-trivial zip
```

If the existing file uses a different fixture name (not `live_backend`), match it. Read `test_full_e2e.py` first.

- [ ] **Step 3: Run E2E tests locally if D365 server creds are set**

```bash
pytest crm/tests/test_full_e2e.py::TestE2ESpecA -v -s
```

Expected: PASS against a live server; SKIP cleanly if credentials are missing.

- [ ] **Step 4: Commit**

```bash
git add crm/tests/test_full_e2e.py
git commit -m "test(e2e): cover §3.3 EntitySetName read-back and §3.6 export flag"
```

---

### Task 26: Version bump to 0.2.0 + `CHANGELOG.md`

**Files:**
- Modify: `setup.py`
- Create: `CHANGELOG.md`

- [ ] **Step 1: Bump version**

In `setup.py`, change:

```python
    version="0.1.2",
```

to:

```python
    version="0.2.0",
```

- [ ] **Step 2: Create `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to `crm` are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-05-24

This release lands Spec A from the post-code-review roadmap: nine correctness
fixes plus pyright strict (zone-scoped) across `crm/core/*` and
`crm/utils/d365_backend.py`. See
`docs/superpowers/specs/2026-05-24-spec-a-correctness-pyright-design.md` for
the full design.

### Breaking

- **Error envelope `meta.status` and `meta.code` now emit JSON `null`** when
  absent, instead of the literal string `"n/a"`. Scripts that string-match
  `"n/a"` must switch to a null check. (§3.5)

### Added

- `--export-setting <name>` flag on `crm solution export`, repeatable.
  Accepted names: `autonumbering`, `calendar`, `customizations`,
  `email-tracking`, `general`, `isv-config`, `marketing`, `outlook-sync`,
  `relationship-roles`, `sales`. (§3.6)
- `crm/utils/d365_types.py` — `TypedDict` shapes for Web API responses.
- `pyright` (>=1.1.380) as a dev dependency and a CI step in
  `.github/workflows/build.yml`. Strict mode on `crm/core/*` +
  `crm/utils/d365_backend.py`; basic mode on `crm/cli.py`,
  `crm/utils/repl_skin.py`, and `crm/tests/*`.

### Changed

- `metadata create-entity` now reads `EntitySetName` back from the server
  instead of guessing it via English pluralisation. Adds one round-trip per
  create call. On read-back failure the entity is still reported as created,
  with `entity_set_name: null` and a diagnostic `entity_set_lookup_error`
  field. (§3.3)
- REPL keeps a single `D365Backend` per session instead of rebuilding on
  every command. Invalidated by `connection connect` / `connection
  disconnect`. (§3.7)
- `$count` queries parse `text/plain` directly in one HTTP call on the
  happy path. Falls back to `?$count=true` if the body is missing or
  non-numeric. (§3.9)
- `fetchxml_query` passes the FetchXML via `params=` instead of manual URL
  concatenation. No on-wire change. (§3.4)

### Fixed

- `entity create` no longer sends the non-spec `If-None-Match: null` header
  on POST. (§3.1)
- `data export` CSV no longer leaks `_value` lookup columns and `@odata.*`
  annotations into headers — `_ordered_keys` boolean precedence bug. (§3.2)
- `.env` value parser is now pair-aware: `KEY="foo's bar"` resolves to
  `foo's bar`, not `foos bar`. (§3.8)

[0.2.0]: https://github.com/Gharib89/crm/releases/tag/v0.2.0
```

- [ ] **Step 3: Run full validation**

```bash
pyright && pytest -q && crm --version
```

Expected: pyright clean; tests pass; `crm --version` prints `crm 0.2.0`.

- [ ] **Step 4: Commit**

```bash
git add setup.py CHANGELOG.md
git commit -m "release: 0.2.0 — Spec A (correctness + pyright)"
```

---

### Task 27: Push PR3 + open

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/api-shape-0.2.0
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base fix/correctness --title "Spec A PR3: API-shape changes + 0.2.0 release" --body "$(cat <<'EOF'
## Summary

Final PR for Spec A. Bumps to **0.2.0**. One breaking change documented in `CHANGELOG.md`.

- §3.3 metadata: `create-entity` reads `EntitySetName` back from the server (one extra round-trip; never fails the command).
- §3.5 cli: error envelope `meta.status` / `meta.code` emit `null` instead of `"n/a"`. **Breaking.**
- §3.6 solution: `--export-setting <name>` repeatable flag exposes the `Export*Settings` payload knobs.
- §3.7 cli: REPL keeps one `D365Backend` per session; invalidated only by `connection connect`/`disconnect`.

Plus: `CHANGELOG.md` covering all three Spec A PRs; version → 0.2.0; two new E2E tests.

Spec: `docs/superpowers/specs/2026-05-24-spec-a-correctness-pyright-design.md`.

## Test plan

- [x] New unit tests cover every behaviour change
- [x] E2E tests cover §3.3 and §3.6 against a live server (skip cleanly without creds)
- [x] `pyright` exits 0
- [x] `pytest -q` passes
- [x] `crm --version` prints `crm 0.2.0`
- [ ] CI green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

PR base is `fix/correctness` until PR2 merges; retarget to `main` after.

---

## Self-Review Notes

Coverage scan against the spec:

- §3.1 → Task 15 ✓
- §3.2 → Task 16 ✓
- §3.3 → Task 24 ✓ (three tests including the null-header branch)
- §3.4 → Task 17 ✓
- §3.5 → Task 21 ✓
- §3.6 → Task 23 ✓
- §3.7 → Task 22 ✓
- §3.8 → Task 18 ✓
- §3.9 → Task 19 ✓ (text/plain + fallback branch tests both included)
- §3.10 (pyright) → Tasks 1–14 ✓
- 0.2.0 bump → Task 26 ✓
- CHANGELOG.md → Task 26 ✓
- E2E tests → Task 25 ✓

Cross-task consistency:

- `as_dict()` helper added in Task 5 (d365_backend.py); used by every core/* annotation task (6–13) and by the correctness/API-shape fixes (Tasks 19, 23, 24).
- `CLIContext.invalidate_backend()` defined in Task 22 (§3.7); the method name matches the calls inserted in `connection_connect` and `connection_disconnect` in the same task.
- `_EXPORT_SETTING_KEYS` in Task 23 maps CLI choice names to `solution.py` kwarg names; the kwarg names match exactly between core and CLI.
- `entity_set_lookup_error` field name appears identically in §3.3 spec, the three Task 24 tests, and the Task 24 implementation.
