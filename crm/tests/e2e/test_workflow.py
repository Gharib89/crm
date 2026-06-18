# pyright: basic
"""E2E tests for workflow commands.

Covers: workflow list, workflow export, workflow migration-assess,
        workflow activate, workflow deactivate.

`workflow run` is already in E2E_SKIP (async side-effects on live records).
`workflow clone`, `workflow delete`, and `workflow import` require creating or
upserting a workflow record via the Web API, which both test orgs reject with
an org-level policy error ("created outside the Microsoft Dynamics 365 Web
application"). These verbs are in E2E_SKIP with that reason.

Safety contract
---------------
Tests that mutate state (activate/deactivate) operate only on an existing
draft custom workflow found at runtime. System workflows are never modified.
If no suitable workflow exists, the test is skipped via ``pytest.skip``.
"""
from __future__ import annotations

import json

import pytest

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
