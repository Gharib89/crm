# pyright: basic
"""E2E tests for chart verbs: list, create, get, delete (system + user charts).

Chart XML is validated server-side (chart areas must match categories, etc.), so
the create tests use a minimal but known-valid datadescription/presentationdescription
pair for the standard `contact` entity — standard field names + styling only, no
org identifiers.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers

# Minimal valid chart XML for `contact`: a doughnut counting contacts grouped by
# preferred contact method. One category ↔ one chart area (the server enforces
# this). Standard fields only — safe as a public fixture.
_DATA_XML = (
    '<datadefinition><fetchcollection><fetch mapping="logical" aggregate="true">'
    '<entity name="contact">'
    '<attribute name="preferredcontactmethodcode" groupby="true" alias="groupby_column" />'
    '<attribute name="fullname" aggregate="count" alias="aggregate_column" />'
    '</entity></fetch></fetchcollection>'
    '<categorycollection><category alias="groupby_column">'
    '<measurecollection><measure alias="aggregate_column" /></measurecollection>'
    '</category></categorycollection></datadefinition>'
)
_PRES_XML = (
    '<Chart><Series><Series ChartType="Doughnut">'
    '<SmartLabelStyle Enabled="True" /><Points /></Series></Series>'
    '<ChartAreas><ChartArea><AxisY /><AxisX /></ChartArea></ChartAreas></Chart>'
)


def _write_xml(tmp_path):
    dd = tmp_path / "data.xml"
    dd.write_text(_DATA_XML, encoding="utf-8")
    pd = tmp_path / "pres.xml"
    pd.write_text(_PRES_XML, encoding="utf-8")
    return str(dd), str(pd)


# ── chart list ────────────────────────────────────────────────────────────────


@covers("chart list")
def test_chart_list_contact(cli):
    """Stock orgs ship system charts for 'contact'; assert non-empty + shape."""
    result = cli(["--json", "chart", "list", "contact"])
    assert result.returncode == 0, (
        f"chart list failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list) and len(items) > 0, env
    first = items[0]
    assert "savedqueryvisualizationid" in first, first
    assert "name" in first, first
    assert "isdefault" in first, first


# ── chart create / get / delete (system) ──────────────────────────────────────


@covers("chart create", "chart get", "chart delete")
@pytest.mark.slow
def test_chart_system_lifecycle(cli, tmp_path, unique):
    """Create a system chart on 'contact', read it back, then delete it."""
    name = f"E2E Chart {unique}"
    dd, pd = _write_xml(tmp_path)

    result = cli([
        "--json", "chart", "create", "contact",
        "--name", name,
        "--data-description", dd,
        "--presentation-description", pd,
        "--no-publish",
    ])
    assert result.returncode == 0, (
        f"chart create failed:\n{result.stderr}\nstdout: {result.stdout}")
    created = json.loads(result.stdout)
    assert created["ok"], created
    chart_id = created["data"]["savedqueryvisualizationid"]
    assert chart_id, created

    try:
        got = cli(["--json", "chart", "get", chart_id])
        assert got.returncode == 0, got.stderr
        env = json.loads(got.stdout)
        assert env["ok"], env
        assert env["data"]["name"] == name
        assert env["data"]["primaryentitytypecode"] == "contact"
        assert "<fetch" in env["data"]["datadescription"]
    finally:
        deleted = cli(["--json", "chart", "delete", chart_id])
        assert deleted.returncode == 0, deleted.stderr
        assert json.loads(deleted.stdout)["data"]["deleted"] is True


# ── chart create / get / delete (user) ────────────────────────────────────────


@covers("chart create", "chart get", "chart delete", "chart list")
@pytest.mark.slow
def test_chart_user_lifecycle(cli, tmp_path, unique):
    """Create a user chart on 'contact' (--user), read it back, then delete it."""
    name = f"E2E User Chart {unique}"
    dd, pd = _write_xml(tmp_path)

    result = cli([
        "--json", "chart", "create", "contact",
        "--name", name,
        "--data-description", dd,
        "--presentation-description", pd,
        "--user", "--no-publish",
    ])
    assert result.returncode == 0, (
        f"chart create --user failed:\n{result.stderr}\nstdout: {result.stdout}")
    created = json.loads(result.stdout)
    assert created["ok"], created
    chart_id = created["data"]["userqueryvisualizationid"]
    assert chart_id, created

    try:
        got = cli(["--json", "chart", "get", chart_id, "--user"])
        assert got.returncode == 0, got.stderr
        env = json.loads(got.stdout)
        assert env["ok"] and env["data"]["name"] == name, env
        # user charts carry no isdefault flag
        assert "isdefault" not in env["data"], env

        listed = cli(["--json", "chart", "list", "contact", "--user"])
        assert listed.returncode == 0, listed.stderr
        ids = [c["userqueryvisualizationid"] for c in json.loads(listed.stdout)["data"]]
        assert chart_id in ids
    finally:
        deleted = cli(["--json", "chart", "delete", chart_id, "--user"])
        assert deleted.returncode == 0, deleted.stderr
        assert json.loads(deleted.stdout)["data"]["deleted"] is True
