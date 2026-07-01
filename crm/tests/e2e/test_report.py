# pyright: basic
"""E2E tests for report verbs: list, create, get, set-category, delete.

The lifecycle exercises a **link report** (`create --url`): it works identically
on on-prem v9.x and Dataverse online and needs no RDL authoring. An RDL upload
(`create --body-file`) is the same verb with a different body column; Dataverse
online additionally requires the RDL's data source to use the fetch data
provider (RDL authoring is out of the CLI's scope — it uploads verbatim), so the
RDL branch is covered by the wire-level unit tests in crm/tests/test_report.py.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers

# ── report list ───────────────────────────────────────────────────────────


@covers("report list")
def test_report_list(cli):
    """Stock orgs ship system reports; assert non-empty + shape."""
    result = cli(["--json", "report", "list"])
    assert result.returncode == 0, (
        f"report list failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list) and len(items) > 0, env
    first = items[0]
    assert "reportid" in first, first
    assert "name" in first, first
    # list returns summary columns only — the RDL body is fetched via `report get`
    assert "bodytext" not in first, first


# ── report create / get / set-category / delete ────────────────────────────


@covers("report create", "report get", "report set-category", "report delete")
@pytest.mark.slow
def test_report_lifecycle(cli, unique, ephemeral_solution):
    """Create a link report, read it back, categorize it, then delete it."""
    name = f"E2E Report {unique}"

    result = cli([
        "--json", "report", "create",
        "--name", name, "--url", "https://example.com/e2e-report",
        "--solution", ephemeral_solution])
    assert result.returncode == 0, (
        f"report create failed:\n{result.stderr}\nstdout: {result.stdout}")
    created = json.loads(result.stdout)
    assert created["ok"], created
    report_id = created["data"]["reportid"]
    assert report_id, created

    try:
        got = cli(["--json", "report", "get", report_id])
        assert got.returncode == 0, got.stderr
        env = json.loads(got.stdout)
        assert env["ok"], env
        assert env["data"]["name"] == name
        assert env["data"]["bodyurl"] == "https://example.com/e2e-report"

        cat = cli([
            "--json", "report", "set-category", report_id, "--category", "sales",
            "--solution", ephemeral_solution])
        assert cat.returncode == 0, cat.stderr
        cat_env = json.loads(cat.stdout)
        assert cat_env["ok"], cat_env
        assert cat_env["data"]["categorycode"] == 1
        # clean up the category record so the report delete is unobstructed
        rc_id = cat_env["data"]["reportcategoryid"]
        if rc_id:
            cli(["--json", "entity", "delete", "reportcategories", rc_id, "--yes"])
    finally:
        deleted = cli(["--json", "report", "delete", report_id])
        assert deleted.returncode == 0, deleted.stderr
        assert json.loads(deleted.stdout)["data"]["deleted"] is True
