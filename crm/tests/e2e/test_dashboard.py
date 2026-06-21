# pyright: basic
"""E2E tests for dashboard verbs: list, create, get, delete (systemform type=0).

A dashboard's FormXml is posted verbatim. The lifecycle test uses a minimal but
server-accepted dashboard layout — one empty tab/section, standard structure
only, placeholder GUIDs (no org identifiers) — verified live on the cloud org.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers

# Minimal valid dashboard FormXml: one tab, one empty section. Placeholder GUIDs
# only — safe as a public fixture.
_FORMXML = (
    '<form><tabs>'
    '<tab showlabel="true" verticallayout="true" '
    'id="{11111111-1111-1111-1111-111111111111}" name="Tab1" '
    'locklevel="0" expanded="true">'
    '<labels><label description="Tab1" languagecode="1033" /></labels>'
    '<columns><column width="100%"><sections>'
    '<section showlabel="true" showbar="false" columns="11" '
    'id="{22222222-2222-2222-2222-222222222222}" name="Section1">'
    '<labels><label description="Section1" languagecode="1033" /></labels>'
    '<rows><row /></rows></section></sections></column></columns>'
    '</tab></tabs></form>'
)


def _write_formxml(tmp_path) -> str:
    f = tmp_path / "dash.xml"
    f.write_text(_FORMXML, encoding="utf-8")
    return str(f)


# ── dashboard list ──────────────────────────────────────────────────────────


@covers("dashboard list")
def test_dashboard_list(cli):
    """Stock orgs ship system dashboards; assert non-empty + shape."""
    result = cli(["--json", "dashboard", "list"])
    assert result.returncode == 0, (
        f"dashboard list failed:\n{result.stderr}\nstdout: {result.stdout}")
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list) and len(items) > 0, env
    first = items[0]
    assert "formid" in first, first
    assert "name" in first, first
    # list returns list columns only — formxml is fetched via `dashboard get`
    assert "formxml" not in first, first


# ── dashboard create / get / delete ───────────────────────────────────────────


@covers("dashboard create", "dashboard get", "dashboard delete")
@pytest.mark.slow
def test_dashboard_lifecycle(cli, tmp_path, unique):
    """Create an org dashboard from FormXml, read it back, then delete it."""
    name = f"E2E Dashboard {unique}"
    formxml = _write_formxml(tmp_path)

    result = cli([
        "--json", "dashboard", "create",
        "--name", name, "--formxml", formxml, "--no-publish"])
    assert result.returncode == 0, (
        f"dashboard create failed:\n{result.stderr}\nstdout: {result.stdout}")
    created = json.loads(result.stdout)
    assert created["ok"], created
    dashboard_id = created["data"]["formid"]
    assert dashboard_id, created

    try:
        got = cli(["--json", "dashboard", "get", dashboard_id])
        assert got.returncode == 0, got.stderr
        env = json.loads(got.stdout)
        assert env["ok"], env
        assert env["data"]["name"] == name
        assert "<form" in env["data"]["formxml"]
        # org-owned dashboards are not bound to a single table
        assert env["data"]["objecttypecode"] == "none", env
    finally:
        deleted = cli(["--json", "dashboard", "delete", dashboard_id])
        assert deleted.returncode == 0, deleted.stderr
        assert json.loads(deleted.stdout)["data"]["deleted"] is True


# ── dashboard add-chart / add-view (ChartGrid tiles) ──────────────────────────

_CHARTGRID_CLASSID = "{e7a81278-8635-4d9e-8d4d-59480b391c5b}"


def _first_id(cli, args, id_field):
    """Discover a live record id (no org GUIDs embedded in the test)."""
    result = cli(["--json", *args])
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)["data"]
    assert rows, f"no rows for {args}"
    return rows[0][id_field]


def _component_cells(formxml):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(formxml)
    return root, [c for c in root.iter("cell") if c.find("control") is not None]


@covers("dashboard add-chart", "dashboard add-view")
@pytest.mark.slow
def test_dashboard_add_chart_and_view(cli, tmp_path, unique):
    """Splice a chart tile then a view tile into a dashboard and read them back.

    Publishes so the Web API GET returns the edited (published) layer — a
    read-back before publish would false-negative. Asserts the protected
    ChartGrid classid landed, the view/visualization refs landed verbatim, each
    component sits in its own section satisfying rowspan == count(<row>), and
    control ids are unique (a duplicate is rejected at publish).
    """
    view_id = _first_id(cli, ["view", "list", "account"], "savedqueryid")
    chart_id = _first_id(
        cli, ["chart", "list", "account"], "savedqueryvisualizationid")

    name = f"E2E ChartGrid {unique}"
    created = cli(["--json", "dashboard", "create", "--name", name,
                   "--formxml", _write_formxml(tmp_path), "--no-publish"])
    assert created.returncode == 0, created.stderr
    dashboard_id = json.loads(created.stdout)["data"]["formid"]

    try:
        added = cli(["--json", "dashboard", "add-chart", dashboard_id,
                     "--view", view_id, "--chart", chart_id,
                     "--rowspan", "4", "--colspan", "2", "--publish"])
        assert added.returncode == 0, (
            f"add-chart failed:\n{added.stderr}\nstdout: {added.stdout}")
        assert json.loads(added.stdout)["data"]["updated"] is True

        added2 = cli(["--json", "dashboard", "add-view", dashboard_id,
                      "--view", view_id, "--mode", "all", "--publish"])
        assert added2.returncode == 0, (
            f"add-view failed:\n{added2.stderr}\nstdout: {added2.stdout}")

        got = cli(["--json", "dashboard", "get", dashboard_id])
        assert got.returncode == 0, got.stderr
        formxml = json.loads(got.stdout)["data"]["formxml"]
        root, cells = _component_cells(formxml)
        assert len(cells) == 2, formxml
        # protected classid intact on every tile
        for cell in cells:
            assert cell.find("control").get("classid", "").lower() == \
                _CHARTGRID_CLASSID, formxml
        # control ids unique (else publish would have rejected the second tile)
        ctrl_ids = [c.find("control").get("id") for c in cells]
        assert len(set(ctrl_ids)) == 2, ctrl_ids
        # refs landed verbatim
        assert view_id.lower() in formxml.lower()
        assert chart_id.lower() in formxml.lower()
        # each component section satisfies rowspan == count(<row>)
        for section in root.iter("section"):
            for cell in section.iter("cell"):
                if cell.find("control") is not None and cell.get("rowspan"):
                    assert int(cell.get("rowspan")) == \
                        len(section.findall("rows/row")), formxml
    finally:
        cli(["--json", "dashboard", "delete", dashboard_id])
