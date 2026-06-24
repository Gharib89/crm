# pyright: basic
"""E2E tests for workflow commands.

Covers: workflow list, workflow export, workflow migration-assess,
        workflow activate, workflow deactivate, workflow run,
        workflow clone, workflow import, workflow delete.

`workflow run` is dispatch-only: it requires a pre-existing on-demand workflow,
so the test resolves the no-op on-demand workflow seeded per ADR 0012 / #503 and
asserts only that dispatch returns an async operation id (`requires_cloud`).

`workflow clone` / `import` / `delete` upsert a workflow definition via the Web
API. This was long assumed blocked by a platform policy ("created outside the
Microsoft Dynamics 365 Web application", 0x80045040), but #534 showed that wall
is XAML-provenance-sensitive, not target-sensitive: it rejects only foreign
hand-authored XAML and accepts genuine designer XAML, on both targets. clone and
import reuse a real workflow's designer XAML, so the upsert is accepted — these
tests clone a *custom* classic workflow (never a system one), create the clone as
a *draft* (never activated), GET-confirm it persisted, then delete it.

Safety contract
---------------
Tests that mutate state (activate/deactivate) operate only on an existing
draft custom workflow found at runtime. System workflows are never modified.
Tests that create (clone/import) only ever create *draft* clones of a *custom*
classic workflow and delete them (finalizer-guarded), leaving the org clean.
If no suitable workflow exists, the test is skipped via ``pytest.skip``.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.conftest import _safe_delete
from crm.tests.e2e.coverage import covers


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_any_workflow_id(backend) -> str:
    """Return the workflowid of any type-1 workflow definition on the org.

    Uses the list_workflows query directly for reliability. Skips the test
    if the org has no workflow definitions at all (should never happen in practice).
    """
    from crm.core import workflow as wf_mod
    items = wf_mod.list_workflows(backend)
    if not items:
        pytest.skip("No workflow definitions found on this org")
    return str(items[0]["workflowid"])


def _find_draft_custom_workflow_id(backend) -> str | None:
    """Return the workflowid of a draft (statecode=0) non-system workflow,
    or None if none is found.

    A non-system workflow has ``ismanaged=false`` and ``type=1`` (definition).
    We pick only background (mode=0) on-demand workflows to avoid touching
    anything with auto-fire triggers.
    """
    rows = backend.get(
        "workflows",
        params={
            "$select": "workflowid,name,statecode,mode,ondemand,ismanaged",
            "$filter": (
                "type eq 1"
                " and statecode eq 0"
                " and ismanaged eq false"
                " and mode eq 0"
                " and ondemand eq true"
            ),
            "$top": "1",
        },
    )
    if not isinstance(rows, dict):
        return None
    value = rows.get("value", [])
    if not value:
        return None
    return str(value[0]["workflowid"])


# Primary entities we can safely create a throwaway record for, keyed by their
# logical name: (entity-set name, minimal create-body factory). ExecuteWorkflow
# takes only the target record's id and infers the entity from the workflow's
# primaryentity, so the throwaway record must be of that entity.
_DISPATCHABLE_ENTITIES = {
    "account": ("accounts", lambda u: {"name": f"E2E-WFRun-{u}"}),
    "contact": ("contacts", lambda u: {"firstname": "E2E", "lastname": f"WFRun-{u}"}),
}


def _find_dispatchable_ondemand_workflow(backend) -> tuple[str, str] | None:
    """Return ``(workflowid, primaryentity)`` of an activated, background,
    on-demand, unmanaged classic workflow whose primary entity we can create a
    throwaway record for (account/contact), or ``None`` if none is found.

    ``category eq 0`` restricts to classic workflows (not actions/BPFs/business
    rules); ``statecode eq 1`` means activated, so it is dispatchable.
    """
    rows = backend.get(
        "workflows",
        params={
            "$select": "workflowid,name,primaryentity",
            "$filter": (
                "type eq 1"
                " and category eq 0"
                " and statecode eq 1"
                " and ismanaged eq false"
                " and mode eq 0"
                " and ondemand eq true"
            ),
        },
    )
    value = rows.get("value", []) if isinstance(rows, dict) else []
    for row in value:
        primary_entity = row.get("primaryentity")
        if primary_entity in _DISPATCHABLE_ENTITIES:
            return str(row["workflowid"]), primary_entity
    return None


def _find_custom_classic_workflow(backend) -> tuple[str, str] | None:
    """Return ``(workflowid, primaryentity)`` of a custom (unmanaged) classic
    workflow definition that clone/import can reuse, or ``None`` if none exists.

    ``type eq 1`` = definition, ``category eq 0`` = classic workflow (clone's
    Tier-1 path — not action/BPF/business-rule), ``ismanaged eq false`` = custom.
    Cloning reuses this workflow's genuine designer XAML and never touches a
    *system* workflow — cloning a system workflow 500s with 0x80040216, out of
    scope per #534.
    """
    rows = backend.get(
        "workflows",
        params={
            "$select": "workflowid,name,primaryentity",
            "$filter": (
                "type eq 1"
                " and category eq 0"
                " and ismanaged eq false"
            ),
            "$top": "1",
        },
    )
    value = rows.get("value", []) if isinstance(rows, dict) else []
    for row in value:
        primary_entity = row.get("primaryentity")
        if primary_entity:
            return str(row["workflowid"]), primary_entity
    return None


_CLONE_SKIP_MSG = (
    "No custom (unmanaged) classic workflow (type=1, category=0) found on this "
    "org to clone. Seed a simple classic on-demand workflow on account or contact "
    "via the web app (e.g. the no-op workflow per ADR 0012 / #503), then re-run."
)


# ── Tests ─────────────────────────────────────────────────────────────────────


@covers("workflow list")
def test_workflow_list(cli):
    """workflow list returns a non-error envelope; real orgs always have workflows."""
    result = cli(["--json", "workflow", "list"])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], list)
    # Real D365 orgs always have at least one workflow definition.
    assert len(env["data"]) > 0, "expected at least one workflow on a live org"


@covers("workflow migration-assess")
def test_workflow_migration_assess(cli):
    """workflow migration-assess returns a list of assessment verdicts (read-only)."""
    result = cli(["--json", "workflow", "migration-assess"])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], list)
    # Each row has the expected assessment shape.
    for row in env["data"]:
        assert "id" in row, f"missing 'id': {row}"
        assert "verdict" in row, f"missing 'verdict': {row}"
        assert row["verdict"] in ("ready", "blocked"), f"unexpected verdict: {row}"


@covers("workflow export")
def test_workflow_export(backend, cli, tmp_path):
    """workflow export retrieves a workflow definition (incl. xaml) to a JSON file.

    Uses any existing workflow on the org — this is a read-only operation.
    """
    import os
    wf_id = _find_any_workflow_id(backend)

    # Test canonical option --output
    out_file = str(tmp_path / "wf_export.json")
    result = cli(["--json", "workflow", "export", wf_id, "--output", out_file])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"]["workflow_id"] == wf_id
    assert os.path.exists(out_file)
    with open(out_file, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["workflowid"] == wf_id

    # Test backward-compatible alias --out
    out_file_compat = str(tmp_path / "wf_export_compat.json")
    result_compat = cli(["--json", "workflow", "export", wf_id, "--out", out_file_compat])
    assert result_compat.returncode == 0, result_compat.stderr
    assert os.path.exists(out_file_compat)


@covers("workflow activate", "workflow deactivate")
def test_workflow_activate_deactivate(backend, cli, request):
    """Activate then deactivate an existing draft custom on-demand workflow.

    Finds a draft background on-demand unmanaged workflow on the org.
    Skips if none is found. Toggles activate→deactivate and verifies statecode
    via a direct GET after each transition. A finalizer ensures deactivation
    even if the test fails mid-way.
    """
    wf_id = _find_draft_custom_workflow_id(backend)
    if wf_id is None:
        pytest.skip(
            "No draft background on-demand unmanaged workflow found on this org; "
            "cannot safely exercise activate/deactivate without one."
        )

    # Register a finalizer to put the workflow back to draft regardless.
    def _restore_draft():
        try:
            backend.patch(
                f"workflows({wf_id})",
                json_body={"statecode": 0, "statuscode": 1},
                etag="*",
            )
        except Exception:
            pass

    request.addfinalizer(_restore_draft)

    # Activate
    act = cli(["--json", "workflow", "activate", wf_id])
    assert act.returncode == 0, f"activate failed: {act.stderr}"
    act_env = json.loads(act.stdout)
    assert act_env["ok"], act_env
    assert act_env["data"]["activated"] is True
    assert act_env["data"]["statecode"] == 1

    # Verify via direct GET
    row = backend.get(f"workflows({wf_id})", params={"$select": "statecode"})
    assert isinstance(row, dict) and row.get("statecode") == 1

    # Deactivate (--yes: guarded verb, non-interactive run)
    deact = cli(["--json", "workflow", "deactivate", wf_id, "--yes"])
    assert deact.returncode == 0, f"deactivate failed: {deact.stderr}"
    deact_env = json.loads(deact.stdout)
    assert deact_env["ok"], deact_env
    assert deact_env["data"]["activated"] is False
    assert deact_env["data"]["statecode"] == 0

    # Verify via direct GET
    row2 = backend.get(f"workflows({wf_id})", params={"$select": "statecode"})
    assert isinstance(row2, dict) and row2.get("statecode") == 0


@pytest.mark.requires_cloud
@covers("workflow run")
def test_workflow_run_dispatches_ondemand(backend, cli, unique, request):
    """Dispatch-only: run a seeded on-demand workflow against a throwaway record.

    Resolves an activated, background, on-demand workflow on account/contact
    (on the CS-trial that is the no-op workflow seeded per ADR 0012 / #503) and
    dispatches it via ``ExecuteWorkflow`` against a throwaway record of the
    workflow's primary entity. Asserts only that the dispatch succeeded (a
    non-null async operation id) — no downstream record effect is checked.
    Skips with instructions if no such workflow is seeded.
    """
    found = _find_dispatchable_ondemand_workflow(backend)
    if found is None:
        pytest.skip(
            "No activated, background, on-demand workflow on a creatable primary "
            "entity (account/contact) found on this org. Seed a no-op on-demand "
            "workflow on account or contact via the web app (ADR 0012 / #503), "
            "then re-run."
        )
    wf_id, primary_entity = found
    entity_set, make_body = _DISPATCHABLE_ENTITIES[primary_entity]

    rec = backend.post(
        entity_set,
        json_body=make_body(unique),
        extra_headers={"Prefer": "return=representation"},
    )
    rec_id = str(rec[f"{primary_entity}id"])
    request.addfinalizer(lambda: _safe_delete(backend, f"{entity_set}({rec_id})"))

    result = cli(["--json", "workflow", "run", wf_id, "--target", rec_id])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"]["workflow_id"] == wf_id
    assert env["data"]["target_id"] == rec_id
    # Dispatch-only: the platform accepted the request and created an async
    # operation. We do not assert any downstream record effect.
    assert env["data"]["async_operation_id"] is not None, env


@covers("workflow clone", "workflow delete")
def test_workflow_clone_then_delete(backend, cli, unique, request):
    """Clone a custom classic workflow as a draft, GET-confirm it persisted, then
    delete it and GET-confirm it is gone.

    Disproves the old E2E_SKIP premise that the Web API rejects the
    workflow-definition upsert (#534): clone reuses the source's genuine designer
    XAML (retargeted to a fresh id), so the platform accepts the upsert on both
    targets. The clone is created onto the source's own primary entity and never
    activated, so the draft can never fire; a finalizer deletes it even if an
    assertion fails mid-test, leaving the org clean.
    """
    found = _find_custom_classic_workflow(backend)
    if found is None:
        pytest.skip(_CLONE_SKIP_MSG)
    src_id, primary_entity = found

    new_name = f"E2E-Clone-{unique}"
    result = cli([
        "--json", "workflow", "clone", src_id,
        "--to-entity", primary_entity, "--no-activate", "--name", new_name,
    ])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    new_id = str(env["data"]["workflow_id"])
    assert env["data"]["activated"] is False, env
    # Clean up the clone even if a later assertion fails.
    request.addfinalizer(lambda: _safe_delete(backend, f"workflows({new_id})"))

    # GET-confirm the clone persisted as a draft.
    row = backend.get(
        f"workflows({new_id})", params={"$select": "workflowid,statecode,name"}
    )
    assert isinstance(row, dict), row
    assert row.get("statecode") == 0, row  # draft
    assert row.get("name") == new_name, row

    # Delete it.
    deleted = cli(["--json", "workflow", "delete", new_id, "--yes"])
    assert deleted.returncode == 0, deleted.stderr
    del_env = json.loads(deleted.stdout)
    assert del_env["ok"], del_env
    assert del_env["data"]["deleted"] is True, del_env

    # GET-confirm it is gone (filtered collection → empty, no 404 to handle).
    gone = backend.get(
        "workflows",
        params={"$select": "workflowid", "$filter": f"workflowid eq {new_id}"},
    )
    gone_rows = gone.get("value", []) if isinstance(gone, dict) else []
    assert gone_rows == [], f"workflow still present after delete: {gone}"


@covers("workflow import")
def test_workflow_import_recreates_draft(backend, cli, unique, tmp_path, request):
    """Import (upsert) a workflow definition from an exported file, creating a
    draft, then delete it — proving the platform accepts the import upsert (#534).

    Builds a self-consistent, importable definition by cloning a custom classic
    workflow (reusing genuine designer XAML), exporting it, and deleting the clone
    so the subsequent import is a genuine create (import upserts by the file's
    workflowid; absent → create). No XAML is authored by hand.
    """
    found = _find_custom_classic_workflow(backend)
    if found is None:
        pytest.skip(_CLONE_SKIP_MSG)
    src_id, primary_entity = found

    # 1. Clone → a self-consistent draft definition we can export.
    clone = cli([
        "--json", "workflow", "clone", src_id,
        "--to-entity", primary_entity, "--no-activate",
        "--name", f"E2E-Import-{unique}",
    ])
    assert clone.returncode == 0, clone.stderr
    clone_env = json.loads(clone.stdout)
    assert clone_env["ok"], clone_env
    wf_id = str(clone_env["data"]["workflow_id"])
    request.addfinalizer(lambda: _safe_delete(backend, f"workflows({wf_id})"))

    # 2. Export it to a file.
    export_file = str(tmp_path / "wf_import.json")
    exported = cli(["--json", "workflow", "export", wf_id, "--output", export_file])
    assert exported.returncode == 0, exported.stderr

    # 3. Delete the clone so the import recreates it (genuine create).
    pre_del = cli(["--json", "workflow", "delete", wf_id, "--yes"])
    assert pre_del.returncode == 0, pre_del.stderr

    # 4. Import the file → upsert recreates the definition.
    imported = cli(["--json", "workflow", "import", "--file", export_file])
    assert imported.returncode == 0, imported.stderr
    imp_env = json.loads(imported.stdout)
    assert imp_env["ok"], imp_env
    assert str(imp_env["data"]["workflow_id"]) == wf_id, imp_env

    # 5. GET-confirm the import recreated the draft (import default --no-activate).
    row = backend.get(
        f"workflows({wf_id})", params={"$select": "workflowid,statecode"}
    )
    assert isinstance(row, dict), row
    assert row.get("statecode") == 0, row  # draft
