# E2E Test Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every D365-touching CLI verb a live e2e test, enforced by an offline coverage gate that fails CI when a command lacks a test, runnable on demand via `pytest -m e2e`.

**Architecture:** Split the monolithic `test_full_e2e.py` into a `crm/tests/e2e/` package (per-group files, shared fixtures). A `coverage.py` registry (`@covers` decorator + lazy Click-tree walker) feeds an offline gate at `crm/tests/test_e2e_coverage_gate.py`. Live tests are opt-in (`D365_E2E=1` + `D365_*` creds), auto-marked `e2e`, and excluded from the default `pytest` run via `addopts`.

**Tech Stack:** pytest, Click (lazy `_LazyJsonAwareGroup`), `requests_mock` (unit only), GitHub Actions (`workflow_dispatch`).

**Spec:** `docs/superpowers/specs/2026-06-12-e2e-test-completeness-design.md`

**Branch discipline:** Execute in a git worktree on `feat/e2e-completeness` (per project CLAUDE.md). Verify with `PYTHONPATH=$WT <main-venv>/bin/python -m pytest`. Commit type `test:`/`ci:` (no version bump). Contoso placeholders only.

---

## File Structure

**Create:**
- `crm/tests/e2e/__init__.py` — empty package marker.
- `crm/tests/e2e/conftest.py` — `live_profile`/`backend` fixtures, scaffolding fixtures, `pytest_collection_modifyitems` auto-marking, `_safe_delete` helper.
- `crm/tests/e2e/coverage.py` — `@covers`, `COVERED`, `LOCAL_GROUPS`, `E2E_SKIP`, `walk_commands`.
- `crm/tests/test_e2e_coverage_gate.py` — the gate (offline, outside `e2e/`).
- `crm/tests/e2e/test_*.py` — one per command group (see Task 6+).
- `crm/tests/test_cli_offline_smoke.py` — new home for the 2 offline tests relocated from `test_full_e2e.py`.
- `.github/workflows/e2e.yml` — on-demand live CI.

**Modify:**
- `pyproject.toml` — add `[tool.pytest.ini_options]` (markers + `addopts`).
- `crm/tests/TEST.md` — rewrite e2e section.
- `CLAUDE.md` — add the coverage-gate rule under "Keep docs in sync with code".
- `.github/PULL_REQUEST_TEMPLATE.md` — add the e2e-coverage checklist line (create if absent).

**Delete:**
- `crm/tests/test_full_e2e.py` — after migration.

---

## Task 1: Scaffold the `e2e/` package, markers, and the offline default

**Files:**
- Create: `crm/tests/e2e/__init__.py`
- Create: `crm/tests/e2e/conftest.py`
- Modify: `pyproject.toml` (add `[tool.pytest.ini_options]`)

- [ ] **Step 1: Create the package marker**

`crm/tests/e2e/__init__.py`:
```python
```
(empty file)

- [ ] **Step 2: Add pytest config to `pyproject.toml`**

Append a new section (no `[tool.pytest.ini_options]` exists today; `build.yml` runs plain `pytest -q` and stays unaffected):
```toml
[tool.pytest.ini_options]
testpaths = ["crm/tests"]
markers = [
    "e2e: live test requiring a reachable D365 server (opt-in via D365_E2E=1)",
    "requires_cloud: only runs against an OAuth/Dataverse target",
    "requires_onprem: only runs against an NTLM/on-prem target",
    "slow: long-running op (metadata publish, solution async import/export)",
]
addopts = "-m 'not e2e'"
```

- [ ] **Step 3: Add the auto-marking hook to `conftest.py`**

`crm/tests/e2e/conftest.py` (first content — fixtures added in Task 2/3):
```python
"""Shared fixtures and auto-marking for the live e2e suite.

Every test collected under this package is marked `e2e` so the default
`addopts = -m 'not e2e'` filter excludes it. The coverage gate lives OUTSIDE
this package (crm/tests/test_e2e_coverage_gate.py) so it is never marked and
runs in the fast offline CI.
"""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/crm/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)
```

- [ ] **Step 4: Verify the default run stays offline**

Run: `pytest -q --co -m e2e`
Expected: collection shows 0 items today (no e2e tests yet) and exits 5 ("no tests collected") — confirms the marker filter is wired.

Run: `pytest -q`
Expected: the existing unit suite still passes; nothing new collected.

- [ ] **Step 5: Commit**

```bash
git add crm/tests/e2e/__init__.py crm/tests/e2e/conftest.py pyproject.toml
git commit -m "test(e2e): scaffold e2e package, markers, offline-by-default addopts"
```

---

## Task 2: Live-profile + backend fixtures with the safety gate

**Files:**
- Modify: `crm/tests/e2e/conftest.py`

Lift the `_live_profile`/`backend` logic from `crm/tests/test_full_e2e.py:41-118`, make it **session-scoped**, and add the `D365_E2E=1` opt-in plus a production-host guard (Safety invariants in the spec).

- [ ] **Step 1: Add the env helpers and safety gate**

Append to `crm/tests/e2e/conftest.py`:
```python
import os

_REQUIRED = ("D365_URL", "D365_USERNAME", "D365_PASSWORD")
# Hosts that must never receive a destructive e2e run. Extend per environment.
_PROD_HOST_MARKERS = ("prod", "live", ".crm.dynamics.com")  # gov/online prod patterns


def _e2e_opted_in() -> bool:
    return os.environ.get("D365_E2E", "").strip() == "1" and all(
        os.environ.get(k) for k in _REQUIRED
    )


def _assert_not_production(url: str) -> None:
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    allow = os.environ.get("D365_E2E_ALLOW_HOST", "").lower()
    if allow and allow in host:
        return
    for marker in _PROD_HOST_MARKERS:
        if marker in host:
            raise RuntimeError(
                f"Refusing to run destructive e2e against host {host!r} "
                f"(matched {marker!r}). Set D365_E2E_ALLOW_HOST to override."
            )
```

- [ ] **Step 2: Add the session-scoped `live_profile` fixture**

Append:
```python
_LIVE_PROFILE = "e2e"


@pytest.fixture(scope="session", autouse=True)
def live_profile(tmp_path_factory):
    """Seed a throwaway profile from D365_* env under an isolated CRM_HOME and
    activate it. The CLI resolves from THIS profile, never the developer's real
    CRM_HOME. Hard-skips unless D365_E2E=1 and credentials are present."""
    if not _e2e_opted_in():
        pytest.skip("e2e opt-in required: set D365_E2E=1 and D365_URL/USERNAME/PASSWORD")
    from crm.core import session as session_mod
    from crm.utils.d365_backend import ConnectionProfile

    _assert_not_production(os.environ["D365_URL"])
    home = tmp_path_factory.mktemp("e2e-crm")
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(home)
    auth = os.environ.get("D365_AUTH", "ntlm").lower()
    secret = os.environ.get("D365_PASSWORD") or os.environ.get("D365_CLIENT_SECRET") or ""
    api_version = os.environ.get("D365_API_VERSION") or ("v9.2" if auth == "oauth" else "v9.1")
    profile = ConnectionProfile(
        name=_LIVE_PROFILE,
        url=os.environ["D365_URL"],
        domain="" if auth == "oauth" else os.environ.get("D365_DOMAIN", ""),
        username="" if auth == "oauth" else os.environ.get("D365_USERNAME", ""),
        api_version=api_version,
        auth_scheme=auth,
        tenant_id=os.environ.get("D365_TENANT_ID"),
        client_id=os.environ.get("D365_CLIENT_ID"),
    )
    session_mod.save_profile(profile)
    session_mod.save_profile_secret_plaintext(_LIVE_PROFILE, secret)
    state = session_mod.load_session("default")
    state["active_profile"] = _LIVE_PROFILE
    session_mod.save_session(state, "default")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(scope="session")
def backend(live_profile):
    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Backend

    resolved = resolve_credentials(_LIVE_PROFILE)
    return D365Backend(resolved.profile, resolved.password, dry_run=False)
```

- [ ] **Step 3: Verify the safety gate skips when not opted in**

Run: `pytest -q -m e2e crm/tests/e2e/` (no `D365_E2E` set)
Expected: any e2e test that exists skips with "e2e opt-in required" (no tests yet → exits 5; revisit after Task 6).

- [ ] **Step 4: Commit**

```bash
git add crm/tests/e2e/conftest.py
git commit -m "test(e2e): session-scoped live_profile/backend with D365_E2E safety gate"
```

---

## Task 3: Shared scaffolding fixtures and helpers

**Files:**
- Modify: `crm/tests/e2e/conftest.py`

- [ ] **Step 1: Add the subprocess runner, `unique`, `target`, and `_safe_delete`**

Append:
```python
import shutil
import subprocess
import sys
import uuid


def _safe_delete(backend, path: str) -> None:
    """Best-effort teardown; never raises so finalizers don't mask test results."""
    try:
        backend.delete(path)
    except Exception:
        pass


def _resolve_cli(name: str = "crm"):
    force = os.environ.get("CRM_FORCE_INSTALLED", "").strip() == "1"
    found = shutil.which(name)
    if found:
        return [found]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    return [sys.executable, "-m", "crm"]


@pytest.fixture(scope="session")
def cli():
    base = _resolve_cli("crm")

    def run(args, check=True, env=None):
        merged = os.environ.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            base + args, capture_output=True, text=True, check=check, env=merged
        )

    return run


@pytest.fixture
def unique():
    """Collision-free suffix for entity/solution names (per-test)."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="session")
def target():
    """'cloud' for OAuth profiles, 'onprem' for NTLM — drives capability markers.
    Reads the same env the live_profile fixture seeds from (robust regardless of
    whether D365Backend exposes the profile)."""
    return "cloud" if os.environ.get("D365_AUTH", "ntlm").lower() == "oauth" else "onprem"
```

- [ ] **Step 2: Add capability-marker enforcement**

Append the hook that turns `requires_cloud`/`requires_onprem` markers into skips based on the live target:
```python
@pytest.fixture(autouse=True)
def _enforce_capability(request):
    if not _e2e_opted_in():
        return
    target_val = "cloud" if os.environ.get("D365_AUTH", "ntlm").lower() == "oauth" else "onprem"
    if request.node.get_closest_marker("requires_cloud") and target_val != "cloud":
        pytest.skip("requires a cloud/OAuth target")
    if request.node.get_closest_marker("requires_onprem") and target_val != "onprem":
        pytest.skip("requires an on-prem/NTLM target")
```

- [ ] **Step 3: Add the session-scoped `ephemeral_entity` fixture**

Append:
```python
@pytest.fixture(scope="session")
def ephemeral_entity(backend):
    """One uniquely-named custom entity for the whole session — backs attribute/
    relationship/form/ribbon tests. Session scope avoids paying the slow on-prem
    create+publish cycle in every module."""
    import uuid as _uuid
    from crm.core import metadata as meta_mod

    suffix = _uuid.uuid4().hex[:8]
    schema = f"new_E2E{suffix}"
    info = meta_mod.create_entity(backend, schema_name=schema, display_name=f"E2E {suffix}")
    yield info["logical_name"]
    try:
        meta_mod.delete_entity(backend, info["logical_name"])
    except Exception as exc:
        pytest.xfail(f"cleanup failed for {info['logical_name']}: {exc}")
```

- [ ] **Step 4: Add the module-scoped `ephemeral_solution` fixture**

Append:
```python
@pytest.fixture(scope="module")
def ephemeral_solution(backend):
    """Throwaway publisher + unmanaged solution for solution-component tests."""
    import uuid as _uuid
    from crm.core import solution as sol_mod

    suffix = _uuid.uuid4().hex[:8]
    prefix = f"e2e{suffix[:4]}"
    pub_name = f"new_e2epub_{suffix}"
    sol_name = f"new_e2esol_{suffix}"
    pub = sol_mod.create_publisher(
        backend, name=pub_name, prefix=prefix,
        option_value_prefix=10000 + (int(suffix, 16) % 90000),
    )
    sol_mod.create_solution(backend, name=sol_name, publisher_unique_name=pub_name)
    yield sol_name
    try:
        sol_mod.uninstall_solution(backend, sol_name, force=True)
        backend.delete(f"publishers({pub['publisherid']})")
    except Exception as exc:
        pytest.xfail(f"cleanup failed for {sol_name}: {exc}")
```

- [ ] **Step 5: Commit**

```bash
git add crm/tests/e2e/conftest.py
git commit -m "test(e2e): shared cli/unique/target/ephemeral fixtures + capability markers"
```

---

## Task 4: Coverage registry and the lazy Click-tree walker (TDD)

**Files:**
- Create: `crm/tests/e2e/coverage.py`
- Test: `crm/tests/test_e2e_coverage_gate.py` (walker tests added here; gate assertion in Task 5)

- [ ] **Step 1: Write the failing walker test**

`crm/tests/test_e2e_coverage_gate.py`:
```python
from crm.tests.e2e.coverage import walk_commands


def test_walk_finds_all_leaves_via_lazy_loader():
    leaves = set(walk_commands())
    # Sanity floor — the keystone rail. A naive `.commands` walk on the lazy root
    # returns 0; the real walker must drive list_commands/get_command.
    assert len(leaves) > 100, f"walk returned only {len(leaves)} leaves; lazy load broke"
    # Known deep leaves across groups:
    for leaf in ("entity create", "metadata add-attribute", "workflow activate",
                 "solution export", "query odata"):
        assert leaf in leaves, f"missing {leaf!r} from walk: {sorted(leaves)[:20]}..."
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest crm/tests/test_e2e_coverage_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: crm.tests.e2e.coverage`.

- [ ] **Step 3: Implement `coverage.py`**

`crm/tests/e2e/coverage.py`:
```python
"""Coverage registry + Click-tree walker for the e2e gate.

`crm.cli.cli` is a `_LazyJsonAwareGroup`: `group.commands` is EMPTY until a
subcommand is loaded. A naive `.commands` recursion returns 0 leaves and the
gate would pass vacuously. The walker therefore drives the lazy loader via
`list_commands`/`get_command` with a real `click.Context`.
"""
from __future__ import annotations

import click

from crm.cli import cli

# ── Coverage registry ────────────────────────────────────────────────────
COVERED: set[str] = set()


def covers(*paths: str):
    """Stamp a test with the command path(s) it exercises. Accepts multiple so
    one lifecycle test can own several verbs (create/get/update/delete)."""
    if not paths:
        raise ValueError("@covers requires at least one command path")

    def deco(fn):
        COVERED.update(paths)
        fn._covers = (*getattr(fn, "_covers", ()), *paths)
        return fn

    return deco


# ── Out-of-scope verbs ─────────────────────────────────────────────────────
# Top-level groups that touch no Web API — unit-tested elsewhere.
LOCAL_GROUPS = frozenset(
    {"profile", "session", "skill", "self-update", "repl", "scaffold"}
)

# D365-touching verbs that genuinely cannot be auto-e2e'd yet. The gate forces a
# reason to be written down. Fill as the gate enumerates the gap (Task 6+).
E2E_SKIP: dict[str, str] = {
    "plugin register-assembly": "needs a prebuilt signed test .dll; tracked in GH issue",
    "plugin register-step": "depends on a registered assembly (see register-assembly)",
    "solution stage-and-upgrade": "needs a managed solution installed first; org-stateful",
    "workflow run": "async side effects on live records; dispatch-only not asserted",
}


# ── Walker ─────────────────────────────────────────────────────────────────
def walk_commands(group: click.Command | None = None,
                  ctx: click.Context | None = None,
                  prefix: tuple[str, ...] = ()):
    """Yield full leaf command paths ('metadata add-attribute', ...)."""
    if group is None:
        group = cli
    if ctx is None:
        ctx = click.Context(group, info_name="crm")
    if not isinstance(group, click.Group):
        yield " ".join(prefix)
        return
    for name in group.list_commands(ctx):       # triggers lazy load on the root
        sub = group.get_command(ctx, name)      # materializes the command
        if sub is None:
            continue
        path = (*prefix, name)
        if isinstance(sub, click.Group):
            yield from walk_commands(sub, click.Context(sub, info_name=name, parent=ctx), path)
        else:
            yield " ".join(path)
```

- [ ] **Step 4: Run the walker test to verify it passes**

Run: `pytest crm/tests/test_e2e_coverage_gate.py::test_walk_finds_all_leaves_via_lazy_loader -v`
Expected: PASS (≈140 leaves).

- [ ] **Step 5: Commit**

```bash
git add crm/tests/e2e/coverage.py crm/tests/test_e2e_coverage_gate.py
git commit -m "test(e2e): lazy Click-tree walker + @covers registry"
```

---

## Task 5: The coverage gate + staleness guard (offline)

**Files:**
- Modify: `crm/tests/test_e2e_coverage_gate.py`

- [ ] **Step 1: Add the auto-discovery + gate assertions**

Append to `crm/tests/test_e2e_coverage_gate.py`:
```python
import importlib
import pkgutil

import crm.tests.e2e as e2e_pkg
from crm.tests.e2e.coverage import COVERED, E2E_SKIP, LOCAL_GROUPS


def _import_all_e2e_test_modules():
    """Populate COVERED by importing every e2e test module. Auto-discovered so a
    new test file is never silently uncounted. Imports must be side-effect-free
    (no module-level skipif touching live env, no connection at import)."""
    for mod in pkgutil.walk_packages(e2e_pkg.__path__, e2e_pkg.__name__ + "."):
        name = mod.name.rsplit(".", 1)[-1]
        if name.startswith("test_"):
            importlib.import_module(mod.name)


def _expected(walked: set[str]) -> set[str]:
    return {lf for lf in walked if lf.split(" ", 1)[0] not in LOCAL_GROUPS} - set(E2E_SKIP)


def test_every_d365_command_has_e2e_coverage():
    _import_all_e2e_test_modules()
    walked = set(walk_commands())
    assert len(walked) > 100, f"walk returned {len(walked)} leaves; lazy load broke"
    missing = _expected(walked) - COVERED
    assert not missing, (
        "D365 commands lacking e2e coverage (add a @covers test or an E2E_SKIP "
        "entry with a reason):\n  " + "\n  ".join(sorted(missing))
    )


def test_no_stale_skips_or_local_groups():
    walked = set(walk_commands())
    first_tokens = {lf.split(" ", 1)[0] for lf in walked}
    stale_local = LOCAL_GROUPS - first_tokens
    stale_skip = set(E2E_SKIP) - walked
    assert not stale_local, f"LOCAL_GROUPS no longer in CLI: {sorted(stale_local)}"
    assert not stale_skip, f"E2E_SKIP entries no longer in CLI: {sorted(stale_skip)}"
```

- [ ] **Step 2: Run the gate to confirm it RED-lists the real gap**

Run: `pytest crm/tests/test_e2e_coverage_gate.py::test_every_d365_command_has_e2e_coverage -v`
Expected: FAIL with a sorted list of ~120 uncovered commands. **This list is the authoritative work queue for Task 6+** — do not trust the file-layout enumeration in the spec.

Run: `pytest crm/tests/test_e2e_coverage_gate.py::test_no_stale_skips_or_local_groups -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add crm/tests/test_e2e_coverage_gate.py
git commit -m "test(e2e): coverage gate with len>100 sanity floor + staleness guard"
```

---

## Task 6: Migrate existing live tests; relocate offline tests; delete monolith

**Files:**
- Create: `crm/tests/e2e/test_connection.py`, `test_entity.py`, `test_query.py`, `test_metadata.py`, `test_optionset.py`, `test_relationship.py`, `test_solution.py`, `test_plugin.py`
- Create: `crm/tests/test_cli_offline_smoke.py`
- Delete: `crm/tests/test_full_e2e.py`

Migrate the 13 live tests from `test_full_e2e.py` verbatim into the matching per-group file, dropping the inline `_have_live_env`/`_live_profile`/`backend` (now in conftest) and adding `@covers(...)` stamps.

- [ ] **Step 1: Create `test_entity.py` from the contact CRUD tests**

`crm/tests/e2e/test_entity.py`:
```python
import os

from crm.tests.e2e.coverage import covers


@covers("entity create", "entity get", "entity update", "entity delete")
def test_contact_crud_roundtrip(backend, request, unique):
    created = backend.post(
        "contacts",
        json_body={"firstname": "CLI", "lastname": f"Test-{unique}"},
        extra_headers={"If-None-Match": "null", "Prefer": "return=representation"},
    )
    cid = created["contactid"]
    request.addfinalizer(lambda: _safe(backend, f"contacts({cid})"))
    got = backend.get(f"contacts({cid})", params={"$select": "firstname"})
    assert got["firstname"] == "CLI"
    backend.patch(f"contacts({cid})", json_body={"telephone1": "+1-555-0001"},
                  extra_headers={"If-Match": "*"})
    assert backend.get(f"contacts({cid})", params={"$select": "telephone1"})["telephone1"] == "+1-555-0001"
    backend.delete(f"contacts({cid})")


def _safe(backend, path):
    try:
        backend.delete(path)
    except Exception:
        pass
```

(Subprocess variant `test_full_contact_workflow` from `test_full_e2e.py:223-251` moves here too, using the `cli` fixture; stamp it `@covers("entity create", "entity get", "entity delete")` — duplicate stamps are idempotent.)

- [ ] **Step 2: Create `test_connection.py`, `test_query.py`, `test_metadata.py`, `test_optionset.py`, `test_relationship.py`, `test_solution.py`, `test_plugin.py`**

Move the corresponding classes/functions from `test_full_e2e.py`:
- `test_connection.py` ← `test_connection_status_json`, `test_whoami_returns_identity`; `@covers("connection status", "connection whoami")`. Add `test_doctor` and `connection test`.
- `test_query.py` ← `test_fetchxml_query_returns_contacts`, `test_metadata_entities_json` query bits; `@covers("query fetchxml", "query odata")`.
- `test_metadata.py` ← `TestSpecDMetadataWriteLive.test_add_attribute_each_kind`, `test_metadata_list_entities`, `TestE2ESpecA.test_e2e_create_custom_entity...`; `@covers("metadata entities", "metadata create-entity", "metadata add-attribute", "metadata delete-entity")`.
- `test_optionset.py` ← `test_optionset_lifecycle`; `@covers("metadata create-optionset", "metadata update-optionset", "metadata get-optionset", "metadata delete-optionset")`.
- `test_relationship.py` ← `test_one_to_many_to_stock_account`; `@covers("metadata create-one-to-many")`.
- `test_solution.py` ← `TestE2ESpecA.test_e2e_solution_export_with_customization_flag`; `@covers("solution export", "solution create", "solution create-publisher")`.
- `test_plugin.py` ← `TestPluginImageE2E`; `@covers("plugin register-image", "plugin unregister-image")`.

- [ ] **Step 3: Relocate the 2 offline tests**

`crm/tests/test_cli_offline_smoke.py` — move `TestDeleteEntityCli` and `TestAddAttributeBooleanDefaultParsing` from `test_full_e2e.py:354-384` verbatim (they use `CliRunner`, no live server, so they belong in the offline unit suite).

- [ ] **Step 4: Delete the monolith**

```bash
git rm crm/tests/test_full_e2e.py
```

- [ ] **Step 5: Verify offline suite still green and gate shrank**

Run: `pytest -q`
Expected: PASS — relocated smoke tests run; no e2e collected.

Run: `pytest crm/tests/test_e2e_coverage_gate.py::test_every_d365_command_has_e2e_coverage`
Expected: still FAIL, but the missing list is now shorter (migrated verbs gone).

- [ ] **Step 6: Commit**

```bash
git add crm/tests/e2e/ crm/tests/test_cli_offline_smoke.py
git rm crm/tests/test_full_e2e.py
git commit -m "test(e2e): migrate live tests to per-group files; relocate offline smoke tests"
```

---

## Task 7: Fill coverage group-by-group until the gate passes (gate-driven, live)

This is the bulk. The gate's failure list (Task 5 Step 2) is the work queue. Each missing command gets a test built from one of the five archetypes below, or an `E2E_SKIP` entry with a reason. Re-run the gate after each group; loop until `test_every_d365_command_has_e2e_coverage` passes.

**Archetype → group mapping:**

| Archetype | Applies to groups |
|-----------|-------------------|
| A. Read-only assert | `connection`, `query` (count/saved/user), `metadata` (read verbs), `async list/get`, `batch service-document`, `security list-*`, `webresource list/get`, `form list/export`, `ribbon list/export`, `solution list/info/components/dependencies`, `workflow list/export`, `describe` |
| B. CRUD lifecycle | `entity` (all verbs), `view create`, `webresource create/update`, `data import`+`data export` |
| C. Metadata write (uses `ephemeral_entity`) | `metadata create/update/delete-attribute`, `metadata *-optionset`, `metadata *-relationship`, `form clone`, `ribbon add-button/remove` |
| D. Solution/component (uses `ephemeral_solution`) | `solution add-component/remove-component/set-version/clone-as-patch/publish/publish-all/export/import`, `app *`, `sla activate`, `workflow activate/deactivate/clone/delete/import` |
| E. Action/function invoke | `action invoke/function`, `batch batch`, `apply apply` |

**Archetype A — read-only:**
```python
from crm.tests.e2e.coverage import covers


@covers("connection whoami")
def test_whoami(backend):
    r = backend.get("WhoAmI")
    assert "UserId" in r and len(r["UserId"]) >= 36
```

**Archetype B — file export (data):**
```python
@covers("data export")
def test_data_export_csv(cli, tmp_path):
    out = tmp_path / "contacts.csv"
    r = cli(["--json", "data", "export", "contacts", "-o", str(out),
             "--top", "2", "--select", "fullname"])
    assert r.returncode == 0, r.stderr
    assert out.exists() and out.read_text().splitlines()[0]  # header present
```

**Archetype C — metadata write on the shared ephemeral entity:**
```python
@covers("metadata create-optionset", "metadata update-optionset",
        "metadata get-optionset", "metadata delete-optionset")
def test_optionset_lifecycle(backend, unique):
    from crm.core import optionsets as os_mod
    name = f"new_e2e_pri_{unique}"
    try:
        os_mod.create_optionset(backend, name=name, display_name="E2E Priority",
                                options=[(1, "Low"), (2, "Medium")])
        os_mod.update_optionset(backend, name, insert=[(7, "Critical")], update=[(2, "Mid")])
        assert os_mod.get_optionset(backend, name) is not None
    finally:
        try:
            os_mod.delete_optionset(backend, name)
        except Exception as exc:
            import pytest
            pytest.xfail(f"cleanup failed for {name}: {exc}")
```

**Archetype D — solution component (uses `ephemeral_solution`):**
```python
@covers("solution add-component", "solution publish")
def test_add_component_and_publish(backend, ephemeral_solution, ephemeral_entity):
    from crm.core import solution as sol_mod
    sol_mod.add_component(backend, ephemeral_solution, component_type="entity",
                          object_id_logical=ephemeral_entity)
    sol_mod.publish_all(backend)  # @covers also stamps "solution publish-all" if exercised
```

**Archetype E — action/function:**
```python
@covers("action function")
def test_whoami_function(cli):
    r = cli(["--json", "action", "function", "WhoAmI"])
    assert r.returncode == 0, r.stderr
```

**Capability gating** — for verbs that differ by target, mark the test:
```python
import pytest

@pytest.mark.requires_cloud
@covers("solution layer-conflicts")
def test_layer_conflicts(backend): ...
```

- [ ] **Step 1: For each missing command, add a test or an `E2E_SKIP` entry**

Loop, one command group per commit:
1. Run the gate, read the missing list.
2. Pick a group; for each of its missing verbs write a test (archetype from the table) **or** add an `E2E_SKIP[path] = "reason"` entry in `coverage.py`.
3. Run the new tests live against the target: `D365_E2E=1 D365_URL=... D365_USERNAME=... D365_PASSWORD=... CRM_FORCE_INSTALLED=1 pytest -m e2e crm/tests/e2e/test_<group>.py -v`
4. Re-run the gate offline; confirm the group's verbs left the missing list.
5. Commit: `git add crm/tests/e2e/test_<group>.py crm/tests/e2e/coverage.py && git commit -m "test(e2e): cover <group> verbs"`

- [ ] **Step 2: Verify the gate passes**

Run: `pytest crm/tests/test_e2e_coverage_gate.py -v`
Expected: PASS — both gate tests green. Every D365 verb is either covered or in `E2E_SKIP` with a reason.

- [ ] **Step 3: Final live sweep**

Run: `D365_E2E=1 <creds> CRM_FORCE_INSTALLED=1 pytest -m e2e -v`
Expected: all live tests pass (or skip on the non-matching target). Record failures and fix before proceeding.

---

## Task 8: On-demand CI workflow

**Files:**
- Create: `.github/workflows/e2e.yml`

- [ ] **Step 1: Write the workflow**

`.github/workflows/e2e.yml`:
```yaml
name: e2e-live
on:
  workflow_dispatch:
  schedule:
    - cron: "0 3 * * 1"   # weekly Monday 03:00 UTC; remove if undesired

concurrency:
  group: e2e-live
  cancel-in-progress: false

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - name: Run live e2e suite
        env:
          D365_E2E: "1"
          D365_URL: ${{ secrets.D365_URL }}
          D365_USERNAME: ${{ secrets.D365_USERNAME }}
          D365_PASSWORD: ${{ secrets.D365_PASSWORD }}
          D365_AUTH: ${{ secrets.D365_AUTH }}
          D365_TENANT_ID: ${{ secrets.D365_TENANT_ID }}
          D365_CLIENT_ID: ${{ secrets.D365_CLIENT_ID }}
          D365_E2E_ALLOW_HOST: ${{ secrets.D365_E2E_ALLOW_HOST }}
          CRM_FORCE_INSTALLED: "1"
        run: pytest -m e2e -v
```

Note: the cloud OAuth test tenant's host matches `.crm.dynamics.com` (a `_PROD_HOST_MARKERS` entry), so `D365_E2E_ALLOW_HOST` must be set to that host in secrets to clear the production guard. `workflow_dispatch`/`schedule` do not expose secrets to fork PRs.

- [ ] **Step 2: Verify YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/e2e.yml'))"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/e2e.yml
git commit -m "ci: on-demand live e2e workflow (workflow_dispatch + weekly, serialized)"
```

---

## Task 9: Docs, PR template, CLAUDE.md rule

**Files:**
- Modify: `crm/tests/TEST.md`
- Modify: `CLAUDE.md`
- Create/Modify: `.github/PULL_REQUEST_TEMPLATE.md`

- [ ] **Step 1: Rewrite the e2e section of `TEST.md`**

Replace the "E2E Test Plan" section with the new structure: the `e2e/` package, the `e2e` marker, the `D365_E2E=1` opt-in, the coverage gate, and the run forms:
```markdown
## Live E2E suite (`crm/tests/e2e/`)

Opt-in: set `D365_E2E=1` plus `D365_URL`/`D365_USERNAME`/`D365_PASSWORD`
(+ OAuth vars for cloud). Default `pytest` excludes e2e via `addopts`.

- Full sweep:   `pytest -m e2e`
- Quick pass:   `pytest -m "e2e and not slow"`
- One group:    `pytest -m e2e crm/tests/e2e/test_entity.py`   (the `-m e2e` is required;
                a bare path is deselected by the default filter and exits 5)
- `pytest -m slow` overrides the default filter and WILL select slow e2e tests.

Coverage is enforced offline by `crm/tests/test_e2e_coverage_gate.py`: every
D365-touching verb must have a `@covers` test or an `E2E_SKIP` entry with a reason.

Full coverage = the UNION of an on-prem run and a cloud run (capability-gated tests
skip on the non-matching target). Record which target produced each result below.
```

- [ ] **Step 2: Add the per-group timings table to `TEST.md`**

Add an empty table to be filled by the first full run (Task 10):
```markdown
### Live run record

| Date | Target | Group | Tests | Passed | Skipped | Duration |
|------|--------|-------|-------|--------|---------|----------|
| | | | | | | |
```

- [ ] **Step 3: Add the gate rule to `CLAUDE.md`**

Under "## Keep docs in sync with code", append a bullet:
```markdown
- **E2E coverage gate** — every new/changed D365-touching command must ship a live
  e2e test under `crm/tests/e2e/` stamped `@covers("<group> <verb>")`, OR an
  `E2E_SKIP` entry with a reason in `crm/tests/e2e/coverage.py`. The offline gate
  (`crm/tests/test_e2e_coverage_gate.py`) fails CI otherwise. Local/meta groups
  (`profile`, `session`, `skill`, `self-update`, `repl`, `scaffold`) are out of scope
  (`LOCAL_GROUPS`).
```

- [ ] **Step 4: Add the PR template line**

`.github/PULL_REQUEST_TEMPLATE.md` (create if absent, else append a checklist item):
```markdown
- [ ] New/changed D365-touching command has an e2e test (`@covers`) or an `E2E_SKIP` entry with a reason (coverage gate enforces this).
```

- [ ] **Step 5: Verify docs build**

Run: `mkdocs build --strict`
Expected: no warnings/errors (CI runs this; stale refs fail it).

- [ ] **Step 6: Commit**

```bash
git add crm/tests/TEST.md CLAUDE.md .github/PULL_REQUEST_TEMPLATE.md
git commit -m "docs: e2e suite run guide, coverage-gate rule, PR-template line"
```

---

## Task 10: Live run, record results, open PR

**Files:**
- Modify: `crm/tests/TEST.md` (fill the run record)

- [ ] **Step 1: Run the full suite against each available target**

```bash
# On-prem crmworx (local):
D365_E2E=1 D365_URL=... D365_USERNAME=... D365_PASSWORD=... D365_AUTH=ntlm \
  D365_E2E_ALLOW_HOST=<onprem-host> CRM_FORCE_INSTALLED=1 \
  pytest -m e2e -v --durations=0 | tee /tmp/e2e-onprem.log
# Cloud (if available): D365_AUTH=oauth + tenant/client vars.
```
Expected: all pass or capability-skip. Per-test durations from `--durations=0`.

- [ ] **Step 2: Fill the `TEST.md` run record**

Populate the table (Task 9 Step 2) with date, target, per-group counts, and durations from the logs. Use Contoso placeholders for any host/identifier shown.

- [ ] **Step 3: Final offline verification**

Run: `pytest -q && pytest crm/tests/test_e2e_coverage_gate.py -v && pyright --pythonpath .venv/bin/python && mkdocs build --strict`
Expected: unit suite green, gate green, no type errors, docs build clean.

- [ ] **Step 4: Commit and open the PR**

```bash
git add crm/tests/TEST.md
git commit -m "docs: record first full e2e run results"
git push -u origin feat/e2e-completeness
gh pr create --title "test(e2e): complete live coverage + offline enforcement gate" \
  --body "Implements docs/superpowers/specs/2026-06-12-e2e-test-completeness-design.md"
```

---

## Self-Review Notes

- **Spec coverage:** §1 split → Task 6; §2 markers/addopts → Task 1; §3 fixtures → Tasks 2–3; §4 gate (lazy walker, sanity floor, pkgutil, multi-path `@covers`, staleness) → Tasks 4–5; §5 constraints/`E2E_SKIP` → Task 4 + Task 7; §6 CI/docs/commit hygiene → Tasks 8–9; Safety invariants → Task 2; coverage-across-targets + runtime budget → Task 9. All covered.
- **`LOCAL_COMMANDS` → `LOCAL_GROUPS`:** the plan refines the spec's name to a prefix-based frozenset of top-level groups (handles `profile add`, `session info`, …). Same intent.
- **The bulk (Task 7) is gate-driven, not enumerated:** complete archetype code is given; the per-verb work queue is the gate's runtime output, per the spec's "gate output is authoritative" decision. This is deliberate, not a placeholder — e2e bodies depend on live CLI signatures discovered against the org.
