"""Core-layer tests for `crm dashboard` (systemform type=0 verbs)."""
# pyright: basic
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
import requests_mock

from crm.core import dashboard
from crm.utils.d365_backend import D365Error

_DASH_ID = "11112222-3333-4444-5555-666677778888"
_NEW_ID = "99998888-7777-6666-5555-444433332222"

# A minimal but valid dashboard FormXml: one tab with one empty section. The
# section carries a leading empty <row/> placeholder — the common real-dashboard
# shape — so the tile-placement logic is exercised against it.
_DASH_FORMXML = (
    '<form><tabs>'
    '<tab name="tab0" id="{aaaaaaaa-0000-0000-0000-000000000001}">'
    '<labels><label description="Tab" languagecode="1033"/></labels>'
    '<columns><column width="100%"><sections>'
    '<section name="sec0" id="{aaaaaaaa-0000-0000-0000-000000000002}">'
    '<labels><label description="Sec" languagecode="1033"/></labels>'
    '<rows><row /></rows></section>'
    '</sections></column></columns></tab>'
    '</tabs></form>'
)


def _cells(formxml: str) -> list[ET.Element]:
    return list(ET.fromstring(formxml).iter("cell"))


def _control(cell: ET.Element) -> ET.Element:
    ctrl = cell.find("control")
    assert ctrl is not None, "cell has no <control>"
    return ctrl


def _params_of(element: ET.Element) -> dict[str, str]:
    parameters = element.find(".//control/parameters")
    assert parameters is not None, "no <control>/<parameters>"
    return {p.tag: (p.text or "") for p in parameters}


def _component_cell(section: ET.Element) -> ET.Element:
    return next(c for c in section.iter("cell") if c.find("control") is not None)


def _rowspan(cell: ET.Element) -> int:
    return int(cell.get("rowspan") or "0")


_DASH_ROW = {
    "formid": _DASH_ID,
    "name": "Sales Overview",
    "objecttypecode": "none",
    "description": "Org sales dashboard",
    "isdefault": False,
    "type": 0,
    "formxml": "<form><tabs/></form>",
}


def _forms_url(backend) -> str:
    return backend.url_for("systemforms")


class TestListDashboards:
    def test_scopes_to_type_0(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_DASH_ROW]})
            result = dashboard.list_dashboards(backend)
        # the read is scoped to dashboards only (systemforms holds every form type)
        assert m.last_request.qs.get("$filter") == ["type eq 0"]
        assert len(result) == 1
        assert result[0]["formid"] == _DASH_ID

    def test_list_omits_formxml(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_DASH_ROW]})
            result = dashboard.list_dashboards(backend)
        # list returns list columns only — formxml is fetched via `dashboard get`
        assert "formxml" not in result[0]
        # the $select must not request the heavy formxml column
        assert "formxml" not in m.last_request.qs.get("$select", [""])[0]


class TestGetDashboard:
    def test_includes_formxml(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"), json=_DASH_ROW)
            result = dashboard.get_dashboard(backend, _DASH_ID)
        assert result["formxml"] == "<form><tabs/></form>"
        assert result["name"] == "Sales Overview"

    def test_invalid_id_raises(self, backend):
        with pytest.raises(D365Error):
            dashboard.get_dashboard(backend, "not-a-guid")

    def test_rejects_non_dashboard_form(self, backend):
        # systemforms is shared — a main-form id (type 2) must not project as one
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={**_DASH_ROW, "type": 2})
            with pytest.raises(D365Error, match="not a dashboard"):
                dashboard.get_dashboard(backend, _DASH_ID)


class TestDeleteDashboard:
    def test_delete(self, backend):
        with requests_mock.Mocker() as m:
            # pre-flight type check, then the delete
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={"formid": _DASH_ID, "type": 0})
            m.delete(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)
            result = dashboard.delete_dashboard(backend, _DASH_ID)
        assert result == {"deleted": True, "formid": _DASH_ID}

    def test_delete_dry_run_previews(self, dry_backend):
        # the pre-flight GET runs even under dry-run (reads-execute); the DELETE
        # is short-circuited by the backend.
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(f"systemforms({_DASH_ID})"),
                  json={"formid": _DASH_ID, "type": 0})
            result = dashboard.delete_dashboard(dry_backend, _DASH_ID)
        assert result == {"_dry_run": True, "would_delete": True, "formid": _DASH_ID}

    def test_delete_refuses_non_dashboard_form(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={"formid": _DASH_ID, "type": 2})
            with pytest.raises(D365Error, match="not a dashboard"):
                dashboard.delete_dashboard(backend, _DASH_ID)
        # the destructive DELETE was never issued
        assert all(r.method != "DELETE" for r in m.request_history)


class TestCreateDashboard:
    _NEW_ID_URL = f"systemforms({_NEW_ID})"

    def _post_mock(self, m, backend):
        m.post(_forms_url(backend), status_code=204,
               headers={"OData-EntityId": backend.url_for(self._NEW_ID_URL)})

    def test_posts_type_0_org_dashboard(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            out = dashboard.create_dashboard(
                backend, name="Sales", formxml="<form/>")
        body = m.last_request.json()
        assert body["type"] == 0
        assert body["name"] == "Sales"
        assert body["formxml"] == "<form/>"
        assert body["objecttypecode"] == "none"
        assert out["created"] is True
        assert out["formid"] == _NEW_ID

    def test_create_dry_run_previews_resolved_body(self, dry_backend):
        out = dashboard.create_dashboard(
            dry_backend, name="Sales", formxml="<form/>", description="d")
        assert out["_dry_run"] is True
        assert out["would_create"]["entity_set"] == "systemforms"
        assert out["would_create"]["body"]["type"] == 0
        assert out["would_create"]["body"]["description"] == "d"

    def test_adds_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            dashboard.create_dashboard(
                backend, name="Sales", formxml="<form/>", solution="MySol")
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_publish_runs_publishallxml(self, backend, monkeypatch):
        called = {}
        monkeypatch.setattr("crm.core.solution.publish_all",
                            lambda b: called.setdefault("published", True))
        with requests_mock.Mocker() as m:
            self._post_mock(m, backend)
            out = dashboard.create_dashboard(
                backend, name="Sales", formxml="<form/>", publish=True)
        assert called.get("published") is True
        assert out["published"] is True


class TestAddChartgridToFormxml:
    """The pure FormXml transform that splices a ChartGrid <cell> in."""

    def _params(self):
        return {
            "TargetEntityType": "account",
            "ViewId": "{cccccccc-0000-0000-0000-000000000001}",
            "IsUserView": "false",
            "AutoExpand": "Fixed",
            "ChartGridMode": "Chart",
            "VisualizationId": "{dddddddd-0000-0000-0000-000000000001}",
        }

    def test_inserts_protected_classid_cell(self):
        out = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML, params=self._params(), label="Accounts")
        cells = _cells(out)
        assert len(cells) == 1  # cell-count went 0 -> 1
        assert _control(cells[0]).get("classid") == dashboard.CHARTGRID_CLASSID

    def test_emits_parameters_verbatim(self):
        out = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML, params=self._params(), label="Accounts")
        got = _params_of(_cells(out)[0])
        assert got["AutoExpand"] == "Fixed"
        assert got["TargetEntityType"] == "account"
        assert got["IsUserView"] == "false"
        assert got["ViewId"] == "{cccccccc-0000-0000-0000-000000000001}"
        assert got["VisualizationId"] == "{dddddddd-0000-0000-0000-000000000001}"

    def _component_section(self, formxml: str) -> ET.Element:
        return next(
            s for s in ET.fromstring(formxml).iter("section")
            if any(c.find("control") is not None for c in s.iter("cell")))

    def test_rowspan_equals_row_count(self):
        out = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML, params=self._params(), label="A", rowspan=4)
        section = self._component_section(out)
        rows = section.findall("rows/row")
        assert _rowspan(_component_cell(section)) == len(rows) == 4

    def test_rowspan_default_one_matches_single_row(self):
        out = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML, params=self._params(), label="A")
        section = self._component_section(out)
        assert _rowspan(_component_cell(section)) == \
            len(section.findall("rows/row")) == 1

    @staticmethod
    def _existing_cell_id(i: int) -> str:
        # A valid placeholder GUID (not a real org id) so the pure-append guard,
        # which keys on a strict GUID regex, actually polices these pre-existing
        # <cell id=...> values against an accidental rewrite.
        return f"{{cccc0000-0000-0000-0000-0000000000{i:02d}}}"

    def _formxml_with_components(self, n: int) -> str:
        cells = "".join(
            f'<row><cell id="{self._existing_cell_id(i)}"><control id="c{i}" '
            f'classid="{dashboard.CHARTGRID_CLASSID}"><parameters/></control>'
            f'</cell></row>' for i in range(n))
        return (
            '<form><tabs><tab name="t" id="{aaaa0000-0000-0000-0000-000000000001}">'
            '<columns><column width="100%"><sections>'
            '<section name="s" id="{aaaa0000-0000-0000-0000-000000000002}">'
            f'<rows>{cells}</rows></section>'
            '</sections></column></columns></tab></tabs></form>')

    def test_refuses_more_than_six_components(self):
        with pytest.raises(D365Error, match="6"):
            dashboard.add_chartgrid_to_formxml(
                self._formxml_with_components(6), params=self._params(), label="A")

    def test_force_overrides_component_cap(self):
        out = dashboard.add_chartgrid_to_formxml(
            self._formxml_with_components(6), params=self._params(),
            label="A", force=True)
        assert len(_cells(out)) == 7

    def test_empty_sections_tab_gets_a_new_section(self):
        # A tab with an empty <sections/> scaffold is fine — the default path
        # adds a fresh section into it (one-component-per-section model).
        empty = (
            '<form><tabs><tab name="t" id="{aaaa0000-0000-0000-0000-000000000009}">'
            '<columns><column width="100%"><sections/></column></columns>'
            '</tab></tabs></form>')
        out = dashboard.add_chartgrid_to_formxml(
            empty, params=self._params(), label="A")
        assert len(_cells(out)) == 1

    def test_missing_sections_scaffold_raises(self):
        no_scaffold = (
            '<form><tabs><tab name="t" id="{aaaa0000-0000-0000-0000-00000000000a}">'
            '<labels><label description="t" languagecode="1033"/></labels>'
            '</tab></tabs></form>')
        with pytest.raises(D365Error, match="scaffold"):
            dashboard.add_chartgrid_to_formxml(
                no_scaffold, params=self._params(), label="A")

    def test_unknown_named_section_raises(self):
        with pytest.raises(D365Error, match="No section"):
            dashboard.add_chartgrid_to_formxml(
                _DASH_FORMXML, params=self._params(), label="A", section="nope")

    def test_named_section_targets_existing(self):
        out = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML, params=self._params(), label="A", section="sec0")
        root = ET.fromstring(out)
        sec0 = next(s for s in root.iter("section") if s.get("name") == "sec0")
        assert any(c.find("control") is not None for c in sec0.iter("cell"))

    def test_refuses_occupied_named_section(self):
        # Co-locating a second component into a section that already has one
        # would break rowspan == count(<row>) for the first cell — reject it.
        with pytest.raises(D365Error, match="already has a component"):
            dashboard.add_chartgrid_to_formxml(
                self._formxml_with_components(1), params=self._params(),
                label="A", section="s")

    def test_preserves_existing_components(self):
        out = dashboard.add_chartgrid_to_formxml(
            self._formxml_with_components(2), params=self._params(), label="A")
        ids = {c.get("id") for c in _cells(out)}
        # the two pre-existing cell ids survive verbatim (guard would raise else)
        assert self._existing_cell_id(0) in ids
        assert self._existing_cell_id(1) in ids
        assert len(ids) == 3  # cell-count +1

    def test_each_tile_gets_its_own_section_keeping_invariant(self):
        # rowspan == count(<row>) cannot hold for two cells sharing one section,
        # so each tile lands in its own section (the one-component-per-section
        # layout model). After two adds, EVERY component section satisfies the
        # invariant.
        once = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML, params=self._params(), label="A", rowspan=4)
        twice = dashboard.add_chartgrid_to_formxml(
            once, params=self._params(), label="B", rowspan=2)
        root = ET.fromstring(twice)
        component_sections = [
            s for s in root.iter("section")
            if any(c.find("control") is not None for c in s.iter("cell"))]
        assert len(component_sections) == 2
        for sec in component_sections:
            rows = sec.findall("rows/row")
            assert _rowspan(_component_cell(sec)) == len(rows)

    def test_distinct_control_ids_for_multiple_tiles(self):
        # Control ids must be unique within a dashboard's FormXml, else the
        # server rejects the second tile at publish ("Duplicate id found for
        # control element"). Adding two tiles must mint distinct control ids.
        once = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML, params=self._params(), label="A")
        twice = dashboard.add_chartgrid_to_formxml(
            once, params=self._params(), label="B")
        control_ids = [_control(c).get("id") for c in _cells(twice)]
        assert len(control_ids) == 2
        assert len(set(control_ids)) == 2, control_ids


_VIEW_ID = "cccccccc-0000-0000-0000-000000000001"
_VIS_ID = "dddddddd-0000-0000-0000-000000000001"


class TestAddChartToDashboard:
    """Orchestrator: validate refs, splice ChartGrid, PATCH formxml."""

    def _mock_reads(self, m, backend, *, view_entity="account", vis_entity="account"):
        m.get(backend.url_for(f"systemforms({_DASH_ID})"),
              json={**_DASH_ROW, "formxml": _DASH_FORMXML})
        m.get(backend.url_for(f"savedqueries({_VIEW_ID})"),
              json={"savedqueryid": _VIEW_ID, "returnedtypecode": view_entity,
                    "name": "Active Accounts"})
        m.get(backend.url_for(f"savedqueryvisualizations({_VIS_ID})"),
              json={"savedqueryvisualizationid": _VIS_ID,
                    "primaryentitytypecode": vis_entity, "name": "By Owner"})
        m.patch(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)

    def test_patches_chartgrid_with_chart_mode(self, backend):
        with requests_mock.Mocker() as m:
            self._mock_reads(m, backend)
            out = dashboard.add_chart_to_dashboard(
                backend, _DASH_ID, view=_VIEW_ID, chart=_VIS_ID)
        patch = next(r for r in m.request_history if r.method == "PATCH")
        params = _params_of(ET.fromstring(patch.json()["formxml"]))
        assert params["ChartGridMode"] == "Chart"
        assert params["TargetEntityType"] == "account"  # derived from the view
        assert params["VisualizationId"].strip("{}").lower() == _VIS_ID
        assert params["AutoExpand"] == "Fixed"
        assert params["IsUserView"] == "false"
        assert out["updated"] is True

    def test_rejects_visualization_entity_mismatch(self, backend):
        with requests_mock.Mocker() as m:
            self._mock_reads(m, backend, view_entity="account", vis_entity="contact")
            with pytest.raises(D365Error, match="entity"):
                dashboard.add_chart_to_dashboard(
                    backend, _DASH_ID, view=_VIEW_ID, chart=_VIS_ID)
        assert all(r.method != "PATCH" for r in m.request_history)


class TestAddViewToDashboard:
    def _mock_reads(self, m, backend):
        m.get(backend.url_for(f"systemforms({_DASH_ID})"),
              json={**_DASH_ROW, "formxml": _DASH_FORMXML})
        m.get(backend.url_for(f"savedqueries({_VIEW_ID})"),
              json={"savedqueryid": _VIEW_ID, "returnedtypecode": "account",
                    "name": "Active Accounts"})
        m.patch(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)

    def test_grid_mode_from_mode_flag(self, backend):
        with requests_mock.Mocker() as m:
            self._mock_reads(m, backend)
            dashboard.add_view_to_dashboard(
                backend, _DASH_ID, view=_VIEW_ID, mode="all", records_per_page=25)
        patch = next(r for r in m.request_history if r.method == "PATCH")
        params = _params_of(ET.fromstring(patch.json()["formxml"]))
        assert params["ChartGridMode"] == "All"
        assert params["RecordsPerPage"] == "25"
        assert "VisualizationId" not in params  # a grid carries no chart

    def test_rejects_unknown_mode(self, backend):
        with pytest.raises(D365Error, match="mode"):
            dashboard.add_view_to_dashboard(
                backend, _DASH_ID, view=_VIEW_ID, mode="bogus")

    def test_dry_run_previews_without_patch(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(f"systemforms({_DASH_ID})"),
                  json={**_DASH_ROW, "formxml": _DASH_FORMXML})
            m.get(dry_backend.url_for(f"savedqueries({_VIEW_ID})"),
                  json={"savedqueryid": _VIEW_ID, "returnedtypecode": "account",
                        "name": "v"})
            out = dashboard.add_view_to_dashboard(
                dry_backend, _DASH_ID, view=_VIEW_ID)
        assert out["_dry_run"] is True and out["would_add"] is True
        assert all(r.method != "PATCH" for r in m.request_history)


_WR_ID = "eeeeeeee-0000-0000-0000-000000000001"


class TestAddIframeToDashboard:
    """Orchestrator: splice an IFRAME tile, PATCH formxml."""

    def _mock(self, m, backend, formxml=_DASH_FORMXML):
        m.get(backend.url_for(f"systemforms({_DASH_ID})"),
              json={**_DASH_ROW, "formxml": formxml})
        m.patch(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)

    def test_patches_iframe_with_protected_classid_and_url(self, backend):
        with requests_mock.Mocker() as m:
            self._mock(m, backend)
            out = dashboard.add_iframe_to_dashboard(
                backend, _DASH_ID, url="https://example.com/x",
                security=True, scrolling=True, border=True, pass_parameters=True)
        patch = next(r for r in m.request_history if r.method == "PATCH")
        cell = _cells(patch.json()["formxml"])[0]
        assert _control(cell).get("classid") == dashboard.IFRAME_CLASSID
        params = _params_of(cell)
        assert params["Url"] == "https://example.com/x"
        # the four typed-boolean flags map to "true"
        assert params["Security"] == "true"
        assert params["Scrolling"] == "true"
        assert params["Border"] == "true"
        assert params["PassParameters"] == "true"
        assert out["updated"] is True
        assert out["action"] == "add-iframe"

    def test_flags_default_to_false(self, backend):
        with requests_mock.Mocker() as m:
            self._mock(m, backend)
            dashboard.add_iframe_to_dashboard(
                backend, _DASH_ID, url="https://example.com/x")
        patch = next(r for r in m.request_history if r.method == "PATCH")
        params = _params_of(_cells(patch.json()["formxml"])[0])
        assert params["Security"] == "false"
        assert params["Scrolling"] == "false"
        assert params["Border"] == "false"
        assert params["PassParameters"] == "false"

    def test_empty_url_refused(self, backend):
        # the documented footgun: an IFRAME with no <Url> renders blank
        with requests_mock.Mocker() as m:
            self._mock(m, backend)
            with pytest.raises(D365Error, match="non-empty"):
                dashboard.add_iframe_to_dashboard(backend, _DASH_ID, url="   ")
        assert all(r.method != "PATCH" for r in m.request_history)


class TestAddWebresourceToDashboard:
    """Orchestrator: validate the web resource exists, splice a tile."""

    def _mock_by_name(self, m, backend, *, name="new_/page.html", wtype=1):
        m.get(backend.url_for(f"systemforms({_DASH_ID})"),
              json={**_DASH_ROW, "formxml": _DASH_FORMXML})
        m.get(backend.url_for("webresourceset"),
              json={"value": [{"webresourceid": _WR_ID, "name": name,
                               "webresourcetype": wtype}]})
        m.patch(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)

    def test_patches_webresource_url_directive(self, backend):
        with requests_mock.Mocker() as m:
            self._mock_by_name(m, backend, name="new_/page.html")
            out = dashboard.add_webresource_to_dashboard(
                backend, _DASH_ID, webresource="new_/page.html")
        patch = next(r for r in m.request_history if r.method == "PATCH")
        cell = _cells(patch.json()["formxml"])[0]
        assert _control(cell).get("classid") == dashboard.IFRAME_CLASSID
        # the resource name is carried via the $webresource: directive in <Url>
        assert _params_of(cell)["Url"] == "$webresource:new_/page.html"
        assert out["webresource"] == "new_/page.html"
        assert "warning" not in out  # an HTML web resource is form-enabled

    def test_resolves_by_id(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={**_DASH_ROW, "formxml": _DASH_FORMXML})
            m.get(backend.url_for(f"webresourceset({_WR_ID})"),
                  json={"webresourceid": _WR_ID, "name": "new_/page.html",
                        "webresourcetype": 1})
            m.patch(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)
            out = dashboard.add_webresource_to_dashboard(
                backend, _DASH_ID, webresource=_WR_ID)
        assert out["webresource"] == "new_/page.html"

    def test_warns_when_not_form_enabled(self, backend):
        # a Script (JScript, type 3) web resource does not render as a tile
        with requests_mock.Mocker() as m:
            self._mock_by_name(m, backend, name="new_/logic.js", wtype=3)
            out = dashboard.add_webresource_to_dashboard(
                backend, _DASH_ID, webresource="new_/logic.js")
        assert "form-enabled" in out["warning"]

    def test_no_spurious_warning_when_type_absent(self, backend):
        # an absent webresourcetype is not evidence the resource won't render —
        # it must not produce a "type None is not form-enabled" warning
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={**_DASH_ROW, "formxml": _DASH_FORMXML})
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": _WR_ID, "name": "new_/p.html"}]})
            m.patch(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)
            out = dashboard.add_webresource_to_dashboard(
                backend, _DASH_ID, webresource="new_/p.html")
        assert "warning" not in out

    def test_missing_webresource_refused(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"systemforms({_DASH_ID})"),
                  json={**_DASH_ROW, "formxml": _DASH_FORMXML})
            m.get(backend.url_for("webresourceset"), json={"value": []})
            with pytest.raises(D365Error):
                dashboard.add_webresource_to_dashboard(
                    backend, _DASH_ID, webresource="new_/missing.html")
        assert all(r.method != "PATCH" for r in m.request_history)


def _dashboard_with(*tiles: str) -> str:
    """Build a dashboard FormXml, one component per section, from <control>
    fragments — each tile becomes its own section/row/cell."""
    sections = "".join(
        f'<section name="sec{i}" id="{{aaaa0000-0000-0000-0000-0000000000{i:02d}}}">'
        f'<rows><row><cell id="{{cccc0000-0000-0000-0000-0000000000{i:02d}}}" '
        f'rowspan="1">{tile}</cell></row></rows></section>'
        for i, tile in enumerate(tiles))
    return (
        '<form><tabs><tab name="t" id="{aaaa0000-0000-0000-0000-0000000000ff}">'
        f'<columns><column width="100%"><sections>{sections}</sections>'
        '</column></columns></tab></tabs></form>')


def _chart_tile(view_id: str, *, control="ChartGrid", vis: str | None = None) -> str:
    vis_el = f"<VisualizationId>{{{vis}}}</VisualizationId>" if vis else ""
    return (f'<control id="{control}" classid="{dashboard.CHARTGRID_CLASSID}">'
            f'<parameters><ViewId>{{{view_id}}}</ViewId>{vis_el}</parameters>'
            f'</control>')


def _iframe_tile(url: str, *, control="IFRAME") -> str:
    return (f'<control id="{control}" classid="{dashboard.IFRAME_CLASSID}">'
            f'<parameters><Url>{url}</Url></parameters></control>')


_RV = "cccccccc-0000-0000-0000-000000000001"
_RV2 = "cccccccc-0000-0000-0000-000000000002"
_RVIS = "dddddddd-0000-0000-0000-000000000001"


class TestRemoveComponentFromFormxml:
    """The pure FormXml transform that detaches one component cell."""

    def _xml(self):
        return _dashboard_with(
            _chart_tile(_RV, control="ChartGrid", vis=_RVIS),
            _chart_tile(_RV2, control="ChartGrid_2"),
            _iframe_tile("https://example.com/a", control="IFRAME"))

    def test_remove_by_view(self):
        out, removed = dashboard.remove_component_from_formxml(
            self._xml(), view=_RV2)
        root = ET.fromstring(out)
        views = [p.text for p in root.iter("ViewId")]
        assert f"{{{_RV2}}}" not in views
        assert f"{{{_RV}}}" in views  # the other chart survives
        assert removed["control_id"] == "ChartGrid_2"

    def test_remove_by_chart(self):
        out, _ = dashboard.remove_component_from_formxml(self._xml(), chart=_RVIS)
        vis = [p.text for p in ET.fromstring(out).iter("VisualizationId")]
        assert f"{{{_RVIS}}}" not in vis

    def test_remove_by_url(self):
        out, removed = dashboard.remove_component_from_formxml(
            self._xml(), url="https://example.com/a")
        assert "example.com" not in out
        assert removed["control_id"] == "IFRAME"

    def test_remove_by_cell_id(self):
        out, removed = dashboard.remove_component_from_formxml(
            self._xml(), cell_id="cccc0000-0000-0000-0000-000000000001")
        ids = {c.get("id") for c in _cells(out)}
        assert "{cccc0000-0000-0000-0000-000000000001}" not in ids
        assert removed["cell_id"] == "{cccc0000-0000-0000-0000-000000000001}"

    def test_remove_by_index(self):
        out, _ = dashboard.remove_component_from_formxml(self._xml(), index=0)
        # index 0 (first component, the ChartGrid with _RV + _RVIS) is gone
        assert f"{{{_RV}}}" not in [p.text for p in ET.fromstring(out).iter("ViewId")]

    def test_index_out_of_range_refused(self):
        with pytest.raises(D365Error, match="out of range"):
            dashboard.remove_component_from_formxml(self._xml(), index=9)

    def test_ambiguous_selector_refused(self):
        # two components share the same ViewId -> the --view selector is ambiguous
        xml = _dashboard_with(_chart_tile(_RV, control="A"),
                              _chart_tile(_RV, control="B"))
        with pytest.raises(D365Error, match="match that selector"):
            dashboard.remove_component_from_formxml(xml, view=_RV)

    def test_no_match_refused(self):
        with pytest.raises(D365Error, match="No dashboard component"):
            dashboard.remove_component_from_formxml(
                self._xml(), url="https://nope.example")

    def test_requires_exactly_one_selector(self):
        with pytest.raises(D365Error, match="exactly one"):
            dashboard.remove_component_from_formxml(self._xml())
        with pytest.raises(D365Error, match="exactly one"):
            dashboard.remove_component_from_formxml(
                self._xml(), view=_RV, chart=_RVIS)

    def test_preserves_other_components(self):
        out, _ = dashboard.remove_component_from_formxml(self._xml(), index=1)
        # the other two components survive untouched (guard would raise otherwise)
        controls = {c.get("id") for c in ET.fromstring(out).iter("control")}
        assert controls == {"ChartGrid", "IFRAME"}

    def test_rowspan_invariant_after_removal(self):
        # a tile in a section with rowspan-padding leaves rowspan == count(<row>)
        once = dashboard.add_chartgrid_to_formxml(
            _DASH_FORMXML,
            params={"ViewId": f"{{{_RV}}}", "ChartGridMode": "Chart"},
            label="A", rowspan=4)
        # now remove it; the emptied section collapses to a single placeholder row
        out, _ = dashboard.remove_component_from_formxml(once, view=_RV)
        root = ET.fromstring(out)
        for section in root.iter("section"):
            cells = [c for c in section.iter("cell") if c.find("control") is not None]
            if cells:
                for cell in cells:
                    assert _rowspan(cell) == len(section.findall("rows/row"))


class TestRemoveComponentFromDashboard:
    def _mock(self, m, backend, formxml):
        m.get(backend.url_for(f"systemforms({_DASH_ID})"),
              json={**_DASH_ROW, "formxml": formxml})
        m.patch(backend.url_for(f"systemforms({_DASH_ID})"), status_code=204)

    def test_removes_and_patches(self, backend):
        xml = _dashboard_with(_chart_tile(_RV, control="ChartGrid"))
        with requests_mock.Mocker() as m:
            self._mock(m, backend, xml)
            out = dashboard.remove_component_from_dashboard(
                backend, _DASH_ID, view=_RV)
        patch = next(r for r in m.request_history if r.method == "PATCH")
        assert f"{{{_RV}}}" not in patch.json()["formxml"]
        assert out["action"] == "remove-component"
        assert out["updated"] is True

    def test_dry_run_previews_would_remove(self, dry_backend):
        xml = _dashboard_with(_chart_tile(_RV, control="ChartGrid"))
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(f"systemforms({_DASH_ID})"),
                  json={**_DASH_ROW, "formxml": xml})
            out = dashboard.remove_component_from_dashboard(
                dry_backend, _DASH_ID, view=_RV)
        assert out["_dry_run"] is True and out["would_remove"] is True
        assert all(r.method != "PATCH" for r in m.request_history)
