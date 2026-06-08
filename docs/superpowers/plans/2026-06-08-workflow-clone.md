# crm workflow clone + export/import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `crm workflow clone <id> --to-entity`, `crm workflow export`, and `crm workflow import` to the existing `workflow` command group, with xaml-retargeting that #143 (clone-entity) can reuse.

**Architecture:** All new logic lands in the existing `crm/core/workflow.py` (pyright **strict**) as pure functions plus backend-driven helpers; thin Click wrappers go in `crm/commands/workflow.py` (`# pyright: basic`). The xaml transform (`retarget_xaml`) is a pure string function tested without any backend. Clone reuses the already-shipped `set_workflow_state` for activation and `crm/core/entity.upsert` for the create-with-explicit-GUID. Action/BPF categories are guarded with a loud failure until a live recon spike (Task 7) proves full support is API-feasible.

**Tech Stack:** Python 3, Click, `requests` (via `crm.utils.d365_backend.D365Backend`), `requests_mock` + `pytest` for tests.

---

## File Structure

- **Modify** `crm/core/workflow.py` — add `retarget_xaml`, `get_workflow`, `clone_workflow_to_entity`, `export_workflow`, `import_workflow`, and a `CATEGORY` guard set. Constants `CATEGORY_*`, `TYPE_*`, `STATE_*` already exist here.
- **Modify** `crm/commands/workflow.py` — add `clone`, `export`, `import` subcommands to `workflow_group`. (Group already registered in `crm/cli.py`; no CLI wiring change.)
- **Create** `crm/tests/test_workflow_clone.py` — unit tests (xaml transform + clone via `requests_mock`).
- **Create** `crm/tests/test_workflow_export_import.py` — export/import round-trip tests.
- **Modify** `README.md`, `docs/how-to/workflow.md`, `crm/skills/SKILL.md` — docs (Task 8).

**TDD collection note (from prior lesson):** each new test file imports only the symbol introduced in its task. Running a test before its function exists fails at collection with `ImportError`/`AttributeError` — that **is** the expected RED. Do not pre-stub.

---

## Task 1: `retarget_xaml` pure transform

**Files:**
- Modify: `crm/core/workflow.py` (add function + imports)
- Test: `crm/tests/test_workflow_clone.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for crm.core.workflow clone helpers."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core.workflow import retarget_xaml

_SRC_ID = "8f9e7a6b-5c4d-3e2f-1a0b-9c8d7e6f5a4b"
_DST_ID = "11112222-3333-4444-5555-666677778888"

_XAML = (
    '<?xml version="1.0" encoding="utf-16"?>\n'
    '<Activity x:Class="XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b" '
    'xmlns:this="clr-namespace:XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b">\n'
    '  <mxsw:GetEntityProperty Attribute="cwx_name" Entity="cwx_ticket" EntityName="cwx_ticket" />\n'
    '  <Comment>lookup field cwx_ticketcategory stays on cwx_ticket</Comment>\n'
    '  <this:XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b.Variables />\n'
    '</Activity>\n'
)


class TestRetargetXaml:
    def test_rewrites_entity_refs_with_word_boundary(self):
        out = retarget_xaml(_XAML, src_entity="cwx_ticket", dst_entity="cwx_ticketclone",
                            src_id=_SRC_ID, dst_id=_DST_ID)
        assert 'Entity="cwx_ticketclone"' in out
        assert 'EntityName="cwx_ticketclone"' in out
        # the trap token must NOT be corrupted into cwx_ticketclonecategory
        assert "cwx_ticketcategory" in out
        assert "cwx_ticketclonecategory" not in out

    def test_rewrites_xclass_and_element_tag_id_dash_stripped(self):
        out = retarget_xaml(_XAML, src_entity="cwx_ticket", dst_entity="cwx_ticketclone",
                            src_id=_SRC_ID, dst_id=_DST_ID)
        dst_stripped = "11112222333344445555666677778888"
        assert f"XrmWorkflow{dst_stripped}" in out
        assert "XrmWorkflow8f9e7a6b5c4d3e2f1a0b9c8d7e6f5a4b" not in out
        # both x:Class and the this: element tag are rewritten
        assert out.count(f"XrmWorkflow{dst_stripped}") == 3

    def test_leaves_unrelated_attribute_names_untouched(self):
        out = retarget_xaml(_XAML, src_entity="cwx_ticket", dst_entity="cwx_ticketclone",
                            src_id=_SRC_ID, dst_id=_DST_ID)
        assert 'Attribute="cwx_name"' in out

    def test_noop_when_nothing_matches(self):
        out = retarget_xaml("<Activity/>", src_entity="cwx_ticket",
                            dst_entity="cwx_ticketclone", src_id=_SRC_ID, dst_id=_DST_ID)
        assert out == "<Activity/>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_workflow_clone.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'retarget_xaml'`.

- [ ] **Step 3: Write minimal implementation**

Add near the top of `crm/core/workflow.py` (after the existing `from typing import Any` block):

```python
import re
```

Add the function after the constants block:

```python
def retarget_xaml(
    xaml: str,
    *,
    src_entity: str,
    dst_entity: str,
    src_id: str,
    dst_id: str,
) -> str:
    """Rewrite a workflow xaml definition to target a new entity and a new id.

    - `XrmWorkflow<src_id-no-dashes>` (the `x:Class` and the matching
      `<this:XrmWorkflow...>` element tags) -> `XrmWorkflow<dst_id-no-dashes>`.
    - Whole-token references to `src_entity` -> `dst_entity`. Word-boundary
      matching protects tokens that merely start with the entity name
      (e.g. `cwx_ticketcategory` is left intact).
    Attribute logical names are not touched.
    """
    src_class = "XrmWorkflow" + src_id.replace("-", "")
    dst_class = "XrmWorkflow" + dst_id.replace("-", "")
    out = xaml.replace(src_class, dst_class)
    out = re.sub(rf"\b{re.escape(src_entity)}\b", dst_entity, out)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_workflow_clone.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add crm/core/workflow.py crm/tests/test_workflow_clone.py
git commit -m "feat(workflow): add retarget_xaml transform for clone"
```

---

## Task 2: `get_workflow` (retrieve the definition)

**Files:**
- Modify: `crm/core/workflow.py`
- Test: `crm/tests/test_workflow_clone.py`

- [ ] **Step 1: Write the failing test**

Append to `crm/tests/test_workflow_clone.py`:

```python
import requests_mock
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


class TestGetWorkflow:
    def test_returns_definition(self, backend):
        from crm.core import workflow
        wf_url = backend.url_for(f"workflows({_SRC_ID})")
        with requests_mock.Mocker() as m:
            m.get(wf_url, json={
                "workflowid": _SRC_ID, "name": "Update request", "category": 0,
                "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
                "mode": 0, "scope": 4, "ondemand": True, "subprocess": False,
                "languagecode": 1033,
            })
            wf = workflow.get_workflow(backend, _SRC_ID)
        assert wf["primaryentity"] == "cwx_ticket"
        assert wf["xaml"] == _XAML

    def test_rejects_activation_copy(self, backend):
        from crm.core import workflow
        wf_url = backend.url_for(f"workflows({_SRC_ID})")
        with requests_mock.Mocker() as m:
            m.get(wf_url, json={"workflowid": _SRC_ID, "type": 2, "name": "X"})
            with pytest.raises(D365Error, match="definition"):
                workflow.get_workflow(backend, _SRC_ID)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_workflow_clone.py::TestGetWorkflow -v`
Expected: FAIL — `AttributeError: module 'crm.core.workflow' has no attribute 'get_workflow'`.

- [ ] **Step 3: Write minimal implementation**

Add to `crm/core/workflow.py`:

```python
_WORKFLOW_SELECT = (
    "workflowid,name,category,primaryentity,type,xaml,"
    "mode,scope,ondemand,subprocess,languagecode,statecode,statuscode"
)


def get_workflow(backend: D365Backend, workflow_id: str) -> dict[str, Any]:
    """Retrieve a workflow definition (type=1) including its xaml.

    Raises if the id points at a type=2 activation copy — callers want the
    definition the server compiles from.
    """
    if not workflow_id:
        raise D365Error("workflow_id is required.")
    result = as_dict(backend.get(
        f"workflows({workflow_id})", params={"$select": _WORKFLOW_SELECT}
    ))
    if result.get("type") == TYPE_ACTIVATION:
        raise D365Error(
            f"Workflow {workflow_id} is a type=2 activation copy; "
            "pass the type=1 definition id instead."
        )
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_workflow_clone.py::TestGetWorkflow -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add crm/core/workflow.py crm/tests/test_workflow_clone.py
git commit -m "feat(workflow): add get_workflow definition retrieval"
```

---

## Task 3: `clone_workflow_to_entity` (Tier 1 + category guard)

**Files:**
- Modify: `crm/core/workflow.py`
- Test: `crm/tests/test_workflow_clone.py`

- [ ] **Step 1: Write the failing test**

Append to `crm/tests/test_workflow_clone.py`:

```python
def _patches(m):
    return [r for r in m.request_history if r.method == "PATCH"]


class TestCloneWorkflow:
    def _src(self, category=0):
        return {
            "workflowid": _SRC_ID, "name": "Update request", "category": category,
            "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
            "mode": 0, "scope": 4, "ondemand": True, "subprocess": False,
            "languagecode": 1033,
        }

    def test_clones_classic_workflow_as_draft(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src())
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", activate=False,
            )
        body = _patches(m)[0].json()
        assert body["primaryentity"] == "cwx_ticketclone"
        assert 'Entity="cwx_ticketclone"' in body["xaml"]
        assert body["name"] == "Update request (Clone)"
        assert body["category"] == 0
        assert out["activated"] is False
        assert out["workflow_id"] == out["workflow_id"]  # a real GUID string
        # only the upsert PATCH happened, no activation PATCH
        assert len(_patches(m)) == 1

    def test_activate_true_compiles_after_create(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src())
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", activate=True,
            )
        # two PATCHes: upsert (draft) then activation
        assert len(_patches(m)) == 2
        activation = _patches(m)[1].json()
        assert activation == {"statecode": 1, "statuscode": 2}
        assert out["activated"] is True

    def test_custom_name_override(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src())
            m.patch(requests_mock.ANY, status_code=204)
            workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", name="My Clone", activate=False,
            )
            assert _patches(m)[0].json()["name"] == "My Clone"

    def test_business_rule_supported(self, backend):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src(category=2))
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.clone_workflow_to_entity(
                backend, _SRC_ID, "cwx_ticketclone", activate=False,
            )
        assert out["category"] == 2

    @pytest.mark.parametrize("category", [3, 4])
    def test_action_and_bpf_fail_loudly(self, backend, category):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src(category=category))
            with pytest.raises(D365Error, match="not yet supported"):
                workflow.clone_workflow_to_entity(backend, _SRC_ID, "cwx_ticketclone")
        # nothing was written
        assert not _patches(m)

    @pytest.mark.parametrize("category", [1, 5])
    def test_dialog_and_modern_flow_out_of_scope(self, backend, category):
        from crm.core import workflow
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_SRC_ID})"), json=self._src(category=category))
            with pytest.raises(D365Error, match="not supported"):
                workflow.clone_workflow_to_entity(backend, _SRC_ID, "cwx_ticketclone")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_workflow_clone.py::TestCloneWorkflow -v`
Expected: FAIL — `AttributeError: ... has no attribute 'clone_workflow_to_entity'`.

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `crm/core/workflow.py`:

```python
from uuid import uuid4

from crm.core import entity as entity_ops
```

Add to `crm/core/workflow.py`:

```python
# Categories clone supports fully via xaml-retarget alone.
_TIER1_CATEGORIES = {CATEGORY_WORKFLOW, CATEGORY_BUSINESS_RULE}
# Categories that need more than xaml (verified live in Task 7); refuse for now.
_NEEDS_MORE_CATEGORIES = {CATEGORY_ACTION, CATEGORY_BPF}
# Categories out of scope for clone entirely.
_UNSUPPORTED_CATEGORIES = {CATEGORY_DIALOG, CATEGORY_MODERN_FLOW}

_CLONE_COPY_FIELDS = ("category", "mode", "scope", "ondemand", "subprocess", "languagecode")
COMPONENT_TYPE_WORKFLOW = 29


def clone_workflow_to_entity(
    backend: D365Backend,
    workflow_id: str,
    target_entity: str,
    *,
    name: str | None = None,
    activate: bool = True,
    solution: str | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
) -> dict[str, Any]:
    """Clone a workflow definition onto another entity.

    Retargets the xaml entity references and the `XrmWorkflow<id>` class to a
    fresh id, creates the clone as a draft (explicit-GUID upsert), then
    optionally activates it (which compiles the xaml). Tier-1 categories
    (classic workflow, business rule) are fully supported; action/BPF fail
    loudly until Task 7 confirms their full create path.
    """
    if not target_entity:
        raise D365Error("target_entity is required.")
    src = get_workflow(backend, workflow_id)
    category = src.get("category")

    if category in _UNSUPPORTED_CATEGORIES:
        raise D365Error(
            f"Cloning category {category} (dialog/modern flow) is not supported."
        )
    if category in _NEEDS_MORE_CATEGORIES:
        raise D365Error(
            f"Cloning category {category} (action/BPF) is not yet supported: it "
            "needs more than an xaml retarget (sdkmessage / stage records). "
            "Use solution export/import for now."
        )
    if category not in _TIER1_CATEGORIES:
        raise D365Error(f"Unknown workflow category {category}; cannot clone.")

    new_id = str(uuid4())
    new_xaml = retarget_xaml(
        src.get("xaml", ""),
        src_entity=src["primaryentity"], dst_entity=target_entity,
        src_id=workflow_id, dst_id=new_id,
    )
    payload: dict[str, Any] = {k: src[k] for k in _CLONE_COPY_FIELDS if k in src}
    payload.update({
        "name": name or f"{src.get('name', 'Workflow')} (Clone)",
        "primaryentity": target_entity,
        "type": TYPE_DEFINITION,
        "xaml": new_xaml,
    })
    entity_ops.upsert(
        backend, "workflows", new_id, payload,
        caller_id=caller_id, caller_object_id=caller_object_id,
    )

    activated = False
    if activate:
        set_workflow_state(
            backend, new_id, activate=True,
            caller_id=caller_id, caller_object_id=caller_object_id,
        )
        activated = True

    if solution:
        from crm.core import solution as solution_ops
        solution_ops.add_solution_component(
            backend, solution=solution,
            component_id=new_id, component_type=COMPONENT_TYPE_WORKFLOW,
        )

    return {
        "workflow_id": new_id,
        "source_id": workflow_id,
        "name": payload["name"],
        "primaryentity": target_entity,
        "category": category,
        "activated": activated,
        "solution": solution,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_workflow_clone.py -v`
Expected: PASS (all `TestCloneWorkflow` cases + earlier tasks).

- [ ] **Step 5: Run pyright (core is strict)**

Run: `pyright --pythonpath .venv/bin/python crm/core/workflow.py`
Expected: 0 errors. (If the build env reports false errors on optional imports, see CLAUDE.md; do not add `# pyright: basic` to a core file.)

- [ ] **Step 6: Commit**

```bash
git add crm/core/workflow.py crm/tests/test_workflow_clone.py
git commit -m "feat(workflow): clone_workflow_to_entity with tiered category guard"
```

---

## Task 4: `crm workflow clone` command

**Files:**
- Modify: `crm/commands/workflow.py`
- Test: `crm/tests/test_workflow_clone.py`

- [ ] **Step 1: Write the failing test**

Append to `crm/tests/test_workflow_clone.py`:

```python
from click.testing import CliRunner


class TestCloneCommand:
    def test_clone_command_invokes_core(self, monkeypatch):
        from crm.commands import workflow as wf_cmd
        called = {}

        def fake_clone(backend, workflow_id, target_entity, **kw):
            called.update(dict(workflow_id=workflow_id, target_entity=target_entity, **kw))
            return {"workflow_id": "new", "activated": kw.get("activate", True)}

        monkeypatch.setattr(wf_cmd.workflow_mod, "clone_workflow_to_entity", fake_clone)

        from crm.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, [
            "workflow", "clone", _SRC_ID, "--to-entity", "cwx_ticketclone",
            "--name", "My Clone", "--no-activate",
        ])
        assert result.exit_code == 0, result.output
        assert called["target_entity"] == "cwx_ticketclone"
        assert called["name"] == "My Clone"
        assert called["activate"] is False
```

> **CLI test wiring:** the entry point is `from crm.cli import cli`, invoked as `CliRunner().invoke(cli, [...])` (see `crm/tests/test_connection_cmd.py`). No `obj=` needed — the group builds its own context. `monkeypatch.setattr` the core function on `wf_cmd.workflow_mod` so the command's lazy `ctx.backend()` is never actually called.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_workflow_clone.py::TestCloneCommand -v`
Expected: FAIL — no `clone` subcommand registered (`exit_code != 0`, "No such command 'clone'").

- [ ] **Step 3: Write minimal implementation**

Add to `crm/commands/workflow.py` after `workflow_run`:

```python
@workflow_group.command("clone")
@click.argument("workflow_id")
@click.option("--to-entity", "target_entity", required=True,
              help="Logical name of the entity to clone the workflow onto.")
@click.option("--name", default=None, help="Name for the clone. Default: '<source> (Clone)'.")
@click.option("--activate/--no-activate", default=True,
              help="Activate the clone after creating it (compiles the xaml). Default: activate.")
@click.option("--solution", default=None, help="Add the clone to this unmanaged solution.")
@_admin_header_options
@pass_ctx
def workflow_clone(ctx: CLIContext, workflow_id, target_entity, name, activate, solution,
                   as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Clone a workflow definition onto another entity (xaml-retargeted)."""
    try:
        info = workflow_mod.clone_workflow_to_entity(
            ctx.backend(), workflow_id, target_entity,
            name=name, activate=activate, solution=solution,
            caller_id=as_user, caller_object_id=as_user_object_id,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
    _journal(ctx, "workflow clone", workflow_id, info)
```

> If `clone_workflow_to_entity` does not accept `suppress_dup_detection`/`bypass_plugins`, do not forward them (it doesn't — keep the call as shown). The admin-header decorator still supplies `as_user`/`as_user_object_id`, which map to `caller_id`/`caller_object_id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_workflow_clone.py::TestCloneCommand -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/commands/workflow.py crm/tests/test_workflow_clone.py
git commit -m "feat(workflow): add 'workflow clone' command"
```

---

## Task 5: `export_workflow` + `import_workflow` core

**Files:**
- Modify: `crm/core/workflow.py`
- Test: `crm/tests/test_workflow_export_import.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for crm.core.workflow export/import."""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock

from crm.core import workflow
from crm.utils.d365_backend import ConnectionProfile, D365Backend

_WF_ID = "8f9e7a6b-5c4d-3e2f-1a0b-9c8d7e6f5a4b"
_XAML = '<Activity x:Class="XrmWorkflowabc" />'


@pytest.fixture
def backend():
    profile = ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


class TestExportImport:
    def test_export_writes_file(self, backend, tmp_path):
        out_file = tmp_path / "wf.json"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_WF_ID})"), json={
                "workflowid": _WF_ID, "name": "Update request", "category": 0,
                "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
            })
            result = workflow.export_workflow(backend, _WF_ID, out_path=str(out_file))
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        assert saved["xaml"] == _XAML
        assert saved["primaryentity"] == "cwx_ticket"
        assert result["out_path"] == str(out_file)

    def test_import_upserts_from_file(self, backend, tmp_path):
        src = tmp_path / "wf.json"
        src.write_text(json.dumps({
            "workflowid": _WF_ID, "name": "Update request", "category": 0,
            "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
            "mode": 0, "scope": 4,
        }), encoding="utf-8")
        with requests_mock.Mocker() as m:
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.import_workflow(backend, file_path=str(src), activate=False)
        patches = [r for r in m.request_history if r.method == "PATCH"]
        assert patches[0].json()["xaml"] == _XAML
        assert out["workflow_id"] == _WF_ID
        assert out["activated"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_workflow_export_import.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'export_workflow'`.

- [ ] **Step 3: Write minimal implementation**

Add to `crm/core/workflow.py`:

```python
import json as _json
from pathlib import Path

_EXPORT_FIELDS = (
    "workflowid", "name", "category", "primaryentity", "type", "xaml",
    "mode", "scope", "ondemand", "subprocess", "languagecode",
)


def export_workflow(
    backend: D365Backend, workflow_id: str, *, out_path: str | None = None
) -> dict[str, Any]:
    """Retrieve a workflow definition and (optionally) write it to a JSON file."""
    wf = get_workflow(backend, workflow_id)
    record = {k: wf[k] for k in _EXPORT_FIELDS if k in wf}
    if out_path:
        Path(out_path).write_text(_json.dumps(record, indent=2), encoding="utf-8")
    return {"workflow_id": workflow_id, "out_path": out_path, "record": record}


def import_workflow(
    backend: D365Backend,
    *,
    file_path: str,
    activate: bool = False,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a workflow definition from a previously exported JSON file."""
    record: dict[str, Any] = _json.loads(Path(file_path).read_text(encoding="utf-8"))
    wf_id = record.get("workflowid")
    if not wf_id:
        raise D365Error(f"{file_path} has no 'workflowid'.")
    payload = {k: v for k, v in record.items() if k != "workflowid"}
    entity_ops.upsert(
        backend, "workflows", wf_id, payload,
        caller_id=caller_id, caller_object_id=caller_object_id,
    )
    activated = False
    if activate:
        set_workflow_state(backend, wf_id, activate=True,
                           caller_id=caller_id, caller_object_id=caller_object_id)
        activated = True
    return {"workflow_id": wf_id, "activated": activated}
```

> Note: `import json as _json` / `from pathlib import Path` may already be partially present — if `re`/`uuid4` imports were added in earlier tasks, keep imports grouped and avoid duplicates.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_workflow_export_import.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run pyright**

Run: `pyright --pythonpath .venv/bin/python crm/core/workflow.py`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/core/workflow.py crm/tests/test_workflow_export_import.py
git commit -m "feat(workflow): add export_workflow/import_workflow round-trip"
```

---

## Task 6: `crm workflow export` / `import` commands

**Files:**
- Modify: `crm/commands/workflow.py`
- Test: `crm/tests/test_workflow_export_import.py`

- [ ] **Step 1: Write the failing test**

Append to `crm/tests/test_workflow_export_import.py`:

```python
from click.testing import CliRunner


class TestExportImportCommands:
    def test_export_command(self, monkeypatch, tmp_path):
        from crm.commands import workflow as wf_cmd
        captured = {}
        monkeypatch.setattr(wf_cmd.workflow_mod, "export_workflow",
                            lambda backend, wid, **kw: captured.update(id=wid, **kw) or {"out_path": kw.get("out_path")})
        from crm.cli import cli
        result = CliRunner().invoke(cli,
            ["workflow", "export", _WF_ID, "--out", str(tmp_path / "x.json")])
        assert result.exit_code == 0, result.output
        assert captured["id"] == _WF_ID

    def test_import_command(self, monkeypatch, tmp_path):
        from crm.commands import workflow as wf_cmd
        captured = {}
        monkeypatch.setattr(wf_cmd.workflow_mod, "import_workflow",
                            lambda backend, **kw: captured.update(**kw) or {"workflow_id": "x", "activated": False})
        f = tmp_path / "x.json"; f.write_text("{}", encoding="utf-8")
        from crm.cli import cli
        result = CliRunner().invoke(cli,
            ["workflow", "import", "--file", str(f)])
        assert result.exit_code == 0, result.output
        assert captured["file_path"] == str(f)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest crm/tests/test_workflow_export_import.py::TestExportImportCommands -v`
Expected: FAIL — "No such command 'export'".

- [ ] **Step 3: Write minimal implementation**

Add to `crm/commands/workflow.py`:

```python
@workflow_group.command("export")
@click.argument("workflow_id")
@click.option("--out", "out_path", default=None, type=click.Path(),
              help="Write the workflow definition to this JSON file. Default: stdout only.")
@pass_ctx
def workflow_export(ctx: CLIContext, workflow_id, out_path):
    """Export a workflow definition (incl. xaml) to a JSON file."""
    try:
        info = workflow_mod.export_workflow(ctx.backend(), workflow_id, out_path=out_path)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@workflow_group.command("import")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True),
              help="Exported workflow JSON file to upsert.")
@click.option("--activate/--no-activate", default=False,
              help="Activate after import. Default: leave as draft.")
@_admin_header_options
@pass_ctx
def workflow_import(ctx: CLIContext, file_path, activate,
                    as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Import (upsert) a workflow definition from an exported JSON file."""
    try:
        info = workflow_mod.import_workflow(
            ctx.backend(), file_path=file_path, activate=activate,
            caller_id=as_user, caller_object_id=as_user_object_id,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
    _journal(ctx, "workflow import", info.get("workflow_id", ""), info)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest crm/tests/test_workflow_export_import.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/commands/workflow.py crm/tests/test_workflow_export_import.py
git commit -m "feat(workflow): add 'workflow export' and 'workflow import' commands"
```

---

## Task 7: Live recon spike (action/BPF) + Tier-1 E2E

This task is investigation against a **live org** (E2E creds in `.env`). It is gated, not blind code — its output decides whether the action/BPF guard from Task 3 is lifted or stays.

- [ ] **Step 1: Capture real xaml shapes**

Against a dev org, run:

```bash
crm workflow list --entity <a real custom entity>
crm workflow export <a classic workflow id> --out /tmp/classic.json
crm workflow export <an action id> --out /tmp/action.json    # if any exist
```

Confirm in `/tmp/classic.json` that the `x:Class` / `<this:XrmWorkflow...>` token shape and id dash-stripping match `retarget_xaml`'s assumptions (Task 1). If they differ, fix `retarget_xaml` and its tests before proceeding.

- [ ] **Step 2: Tier-1 clone E2E (manual, documented)**

```bash
crm workflow clone <classic workflow id> --to-entity <cloned entity> --no-activate
crm workflow clone <classic workflow id> --to-entity <cloned entity>   # activates
```

Verify the clone exists, is activated, and behaves identically. Record the **exact field set** the platform required on create — if the upsert failed for a missing field, add it to `_CLONE_COPY_FIELDS` and add a regression test asserting that field is forwarded.

- [ ] **Step 3: Decide action/BPF**

Investigate whether an activated cloned **action** needs separate sdkmessage/message-pair records, and whether a **BPF** clone's backing entity + stage records + `processid` are API-writable. Record findings in the spec.

- Outcome A — feasible: file a follow-up issue with the concrete create sequence; keep the loud-fail guard until that issue lands.
- Outcome B — not API-feasible: keep the loud-fail guard permanently; note it in `docs/how-to/workflow.md` as a known limitation.

In **both** outcomes the shipped behavior is the Task-3 guard (loud fail, no half-clone), so no code change is required to merge this PR.

- [ ] **Step 4: Commit any retarget/field fixes**

```bash
git add crm/core/workflow.py crm/tests/test_workflow_clone.py docs/superpowers/specs/2026-06-08-workflow-clone-design.md
git commit -m "test(workflow): align clone with live-org field requirements"
```

---

## Task 8: Docs

**Files:**
- Modify: `README.md`, `docs/how-to/workflow.md`, `crm/skills/SKILL.md`

- [ ] **Step 1: README capability lines**

Under the workflow section of `README.md`, add:

```markdown
- `crm workflow clone <id> --to-entity <entity>` — duplicate a workflow onto another entity (xaml-retargeted; classic workflows and business rules; action/BPF not yet supported).
- `crm workflow export <id> --out <file>` / `crm workflow import --file <file>` — round-trip a workflow definition (incl. xaml) as JSON.
```

- [ ] **Step 2: how-to/workflow.md**

Add sections for `clone`, `export`, `import` with a worked example (clone a classic workflow onto a cloned entity, then activate). Note the action/BPF limitation per Task 7's outcome.

- [ ] **Step 3: SKILL.md entries**

In `crm/skills/SKILL.md`, add `workflow clone`/`export`/`import` to the workflow command list with one-line usage each, matching the existing entry style.

- [ ] **Step 4: Build docs strict**

Run: `mkdocs build --strict`
Expected: builds with no warnings (CI gate).

- [ ] **Step 5: Full test + lint sweep**

Run: `pytest crm/tests/test_workflow_clone.py crm/tests/test_workflow_export_import.py -v && pyright --pythonpath .venv/bin/python crm/core/workflow.py`
Expected: all pass, 0 pyright errors.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/how-to/workflow.md crm/skills/SKILL.md
git commit -m "docs(workflow): document clone/export/import"
```

---

## Self-Review

**Spec coverage:**
- `workflow clone` xaml-retarget → Tasks 1–4. ✓
- Tiered category support (classic/business-rule full; action/BPF loud-fail; dialog/modern-flow out) → Task 3 guard + Task 7 recon. ✓
- `export`/`import` round-trip → Tasks 5–6. ✓
- `--solution` (component type 29), `--activate/--no-activate`, `--name` defaults → Task 3/4. ✓
- xaml rules (type=1 master, fresh id, `x:Class`/`this:` rewrite, word-boundary entity ref, attribute names untouched) → Tasks 1–2. ✓
- Reuse of `set_workflow_state` for activation, `entity.upsert` for explicit-GUID create → Task 3. ✓
- Docs (README/how-to/SKILL, cli.md auto-gen, Conventional Commit, `Closes #144`) → Task 8 + PR. ✓
- Tests: `retarget_xaml` unit + clone via requests_mock + E2E → Tasks 1,3,7. ✓

**Placeholder scan:** No "TBD"/"add error handling"-style steps; every code step shows code. CLI test entry point confirmed as `from crm.cli import cli` (per `test_connection_cmd.py`). The only deliberately-live step is Task 7's recon spike, which is explicitly an investigation whose default shipped behavior is the Task-3 guard — no unwritten code blocks merging.

**Type consistency:** `retarget_xaml(xaml, *, src_entity, dst_entity, src_id, dst_id)`, `get_workflow(backend, workflow_id)`, `clone_workflow_to_entity(backend, workflow_id, target_entity, *, name, activate, solution, caller_id, caller_object_id)`, `export_workflow(backend, workflow_id, *, out_path)`, `import_workflow(backend, *, file_path, activate, caller_id, caller_object_id)` — names/signatures consistent across tasks and tests. `COMPONENT_TYPE_WORKFLOW = 29`, `_CLONE_COPY_FIELDS`, `_EXPORT_FIELDS` referenced consistently.
