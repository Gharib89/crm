# pyright: basic
"""E2E tests for chart verbs: list, create, get, delete."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from crm.tests.e2e.coverage import covers

_DATA_XML = (
    '<datadefinition>'
    '<fetchcollection>'
    '<fetch mapping="logical" aggregate="true"><entity name="contact">'
    '<attribute name="statecode" groupby="true" alias="groupby_column" />'
    '<attribute name="contactid" aggregate="count" alias="aggregate_column" />'
    '</entity></fetch>'
    '</fetchcollection>'
    '<categorycollection>'
    '<category alias="groupby_column">'
    '<measurecollection><measure alias="aggregate_column" /></measurecollection>'
    '</category>'
    '</categorycollection>'
    '</datadefinition>'
)
_PRES_XML = (
    '<Chart>'
    '<Series><Series ChartType="Column" IsValueShownAsLabel="True">'
    '<SmartLabelStyle Enabled="True" /><Points /></Series></Series>'
    '<ChartAreas><ChartArea BorderColor="White" BorderDashStyle="Solid">'
    '<AxisY><MajorGrid LineColor="239, 242, 246" /></AxisY>'
    '<AxisX><MajorGrid Enabled="False" /></AxisX>'
    '</ChartArea></ChartAreas>'
    '<Titles><Title Alignment="TopLeft" /></Titles>'
    '</Chart>'
)


# ── chart list ───────────────────────────────────────────────────────────────


@covers("chart list")
def test_chart_list_contact(cli):
    """Every D365 org ships system charts for 'contact'; assert non-empty list + shape."""
    result = cli(["--json", "chart", "list", "contact"])
    assert result.returncode == 0, (
        f"chart list failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list)
    assert len(items) > 0, "chart list returned empty list for 'contact'"
    first = items[0]
    assert "savedqueryvisualizationid" in first, f"id missing: {first}"
    assert "name" in first, f"name missing: {first}"
    assert "isdefault" in first, f"isdefault missing: {first}"


# ── chart create / get / delete (system) ─────────────────────────────────────


@covers("chart create", "chart get", "chart delete")
@pytest.mark.slow
def test_chart_create_get_delete_system(backend, cli, request, unique):
    """Create a system chart on 'contact', get it by ID, then delete it."""
    chart_name = f"E2E Chart {unique}"
    created_id: list[str] = []

    def _cleanup():
        for cid in created_id:
            try:
                backend.delete(f"savedqueryvisualizations({cid})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # Create
    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as df,
        tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as pf,
    ):
        df.write(_DATA_XML)
        pf.write(_PRES_XML)
        data_path = df.name
        pres_path = pf.name

    try:
        result = cli([
            "--json", "chart", "create", "contact",
            "--name", chart_name,
            "--data-description", data_path,
            "--presentation-description", pres_path,
            "--no-publish",
        ])
        assert result.returncode == 0, (
            f"chart create failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        cid = env["data"]["savedqueryvisualizationid"]
        assert cid, "no savedqueryvisualizationid in response"
        created_id.append(cid)

        # Get
        result = cli(["--json", "chart", "get", cid])
        assert result.returncode == 0, (
            f"chart get failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        got = env["data"]
        assert got["savedqueryvisualizationid"] == cid
        assert got["name"] == chart_name

        # Delete
        result = cli(["--json", "chart", "delete", cid])
        assert result.returncode == 0, (
            f"chart delete failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        assert env["data"]["deleted"] is True
        created_id.clear()  # successfully deleted — no need for cleanup
    finally:
        for p in (data_path, pres_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ── chart create / get / delete (user) ───────────────────────────────────────


@covers("chart create --user", "chart get --user", "chart delete --user")
@pytest.mark.slow
def test_chart_create_get_delete_user(backend, cli, request, unique):
    """Create a user chart, get it by ID, delete it — exercises the user lifecycle."""
    chart_name = f"E2E User Chart {unique}"
    created_id: list[str] = []

    def _cleanup():
        for cid in created_id:
            try:
                backend.delete(f"userqueryvisualizations({cid})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as df,
        tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as pf,
    ):
        df.write(_DATA_XML)
        pf.write(_PRES_XML)
        data_path = df.name
        pres_path = pf.name

    try:
        result = cli([
            "--json", "chart", "create", "contact",
            "--name", chart_name,
            "--data-description", data_path,
            "--presentation-description", pres_path,
            "--user",
            "--no-publish",
        ])
        assert result.returncode == 0, (
            f"chart create --user failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        cid = env["data"]["userqueryvisualizationid"]
        assert cid, "no userqueryvisualizationid in response"
        created_id.append(cid)

        # Get
        result = cli(["--json", "chart", "get", cid, "--user"])
        assert result.returncode == 0, (
            f"chart get --user failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        assert env["data"]["userqueryvisualizationid"] == cid
        assert env["data"]["name"] == chart_name

        # Delete
        result = cli(["--json", "chart", "delete", cid, "--user"])
        assert result.returncode == 0, (
            f"chart delete --user failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        assert env["data"]["deleted"] is True
        created_id.clear()
    finally:
        for p in (data_path, pres_path):
            try:
                os.unlink(p)
            except OSError:
                pass
