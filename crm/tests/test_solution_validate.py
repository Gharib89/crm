# pyright: basic
"""Tests for offline solution validation (#141)."""
from __future__ import annotations

import re
import zipfile

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution_validate as sv
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


# ── fixtures ────────────────────────────────────────────────────────────────

def _make_pkg(path, solution_xml, customizations_xml, content_types=True):
    """Write a minimal solution zip (solution.xml + customizations.xml [+ Content_Types])."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("solution.xml", solution_xml)
        zf.writestr("customizations.xml", customizations_xml)
        if content_types:
            zf.writestr("[Content_Types].xml", "<Types/>")


def _sol(roots=""):
    return (
        '<?xml version="1.0"?>\n'
        "<ImportExportXml><SolutionManifest><UniqueName>cwx_test</UniqueName>"
        f"<Managed>0</Managed><RootComponents>{roots}</RootComponents>"
        "</SolutionManifest></ImportExportXml>"
    )


def _cust(optionsets="", dashboards="", webresources="", entities="", forms=""):
    return (
        '<?xml version="1.0"?>\n'
        f"<ImportExportXml><Entities>{entities}</Entities>"
        f"<optionsets>{optionsets}</optionsets>"
        f"<InteractionCentricDashboards>{dashboards}</InteractionCentricDashboards>"
        f"<WebResources>{webresources}</WebResources>{forms}</ImportExportXml>"
    )


@pytest.fixture
def backend() -> D365Backend:
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso", domain="C",
        username="u", api_version="v9.2", verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


# ── Task 1: package-level checks ──────────────────────────────────────────────

class TestPackageChecks:
    def test_good_empty_package_is_valid(self, tmp_path):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol(), _cust())
        report = sv.validate_solution(p)
        assert report["valid"] is True
        assert report["findings"] == []
        assert "package" in report["checks_run"]

    def test_missing_customizations_member_is_fatal(self, tmp_path):
        p = tmp_path / "nocust.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("solution.xml", _sol())
            zf.writestr("[Content_Types].xml", "<Types/>")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any(f["check"] == "package" and "customizations.xml" in f["message"]
                   for f in report["findings"])
        assert report["checks_run"] == ["package"]

    def test_not_a_zip_is_fatal_finding(self, tmp_path):
        p = tmp_path / "junk.zip"
        p.write_bytes(b"not a zip")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any(f["check"] == "package" for f in report["findings"])

    def test_unparseable_xml_is_fatal(self, tmp_path):
        p = tmp_path / "bad.zip"
        _make_pkg(p, _sol(), "<ImportExportXml><unclosed>")
        report = sv.validate_solution(p)
        assert report["valid"] is False
        assert any("well-formed" in f["message"] for f in report["findings"])

    def test_finding_envelope_shape(self, tmp_path):
        p = tmp_path / "nocust.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("solution.xml", _sol())
        report = sv.validate_solution(p)
        f = report["findings"][0]
        assert set(f.keys()) == {"severity", "check", "message", "component", "location"}


# ── Task 2: root-component parity ─────────────────────────────────────────────

_DASH_GUID = "11111111-1111-1111-1111-111111111111"


class TestRootParity:
    def test_optionset_missing_from_rootcomponents(self, tmp_path):
        # Issue class #1: optionset in <optionsets> but not in <RootComponents>.
        p = tmp_path / "bad_optionset.zip"
        _make_pkg(p, _sol(), _cust(optionsets='<optionset Name="cwx_slatier"/>'))
        report = sv.validate_solution(p)
        assert report["valid"] is False
        errs = [f for f in report["findings"] if f["check"] == "root-parity"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_slatier"
        assert "not declared in <RootComponents>" in errs[0]["message"]

    def test_dashboard_missing_from_rootcomponents(self, tmp_path):
        # Issue class #3: dashboard (type 60) in node but not in <RootComponents>.
        p = tmp_path / "bad_dashboard.zip"
        _make_pkg(p, _sol(),
                  _cust(dashboards=f"<Dashboard><FormId>{_DASH_GUID}</FormId></Dashboard>"))
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "root-parity"]
        assert len(errs) == 1
        assert errs[0]["component"] == _DASH_GUID

    def test_rootcomponent_with_no_definition(self, tmp_path):
        # Reverse direction: declared in <RootComponents> but absent from customizations.
        p = tmp_path / "orphan_root.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_ghost"/>'), _cust())
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "root-parity"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_ghost"
        assert "no definition in customizations.xml" in errs[0]["message"]

    def test_clean_parity_is_valid(self, tmp_path):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_slatier"/>'),
                  _cust(optionsets='<optionset Name="cwx_slatier"/>'))
        report = sv.validate_solution(p)
        assert report["valid"] is True
        assert "root-parity" in report["checks_run"]


# ── Task 3: $webresource: ribbon refs ─────────────────────────────────────────

def _ribbon(*refs):
    cmds = "".join(
        f'<CommandDefinition Id="c{i}"><JavaScriptFunction Library="$webresource:{r}"/>'
        f"</CommandDefinition>"
        for i, r in enumerate(refs)
    )
    return f"<Entity><RibbonDiffXml><CommandDefinitions>{cmds}</CommandDefinitions></RibbonDiffXml></Entity>"


class TestWebresourceRefs:
    def test_unresolved_ref_is_error(self, tmp_path):
        p = tmp_path / "bad_ref.zip"
        _make_pkg(p, _sol(), _cust(entities=_ribbon("cwx_/missing.js")))
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "webresource-ref"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_/missing.js"

    def test_ref_resolved_in_package_is_ok(self, tmp_path):
        p = tmp_path / "good_ref.zip"
        _make_pkg(p, _sol(),
                  _cust(entities=_ribbon("cwx_/present.js"),
                        webresources="<WebResource><Name>cwx_/present.js</Name></WebResource>"))
        report = sv.validate_solution(p)
        assert [f for f in report["findings"] if f["check"] == "webresource-ref"] == []

    def test_ref_resolved_against_org(self, tmp_path, backend):
        p = tmp_path / "org_ref.zip"
        _make_pkg(p, _sol(), _cust(entities=_ribbon("cwx_/inorg.js")))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"webresourceset"),
                  json={"value": [{"webresourceid": "x"}]})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "webresource-ref"] == []


# ── Task 4: global option-set bindings ────────────────────────────────────────

def _attr_global_optionset(name):
    return (f'<Entity><attributes><attribute><OptionSet Name="{name}">'
            f"<IsGlobal>1</IsGlobal></OptionSet></attribute></attributes></Entity>")


class TestOptionsetBindings:
    def test_undeclared_global_binding_is_error(self, tmp_path):
        p = tmp_path / "bad_os.zip"
        _make_pkg(p, _sol(), _cust(entities=_attr_global_optionset("cwx_missingset")))
        report = sv.validate_solution(p)
        errs = [f for f in report["findings"] if f["check"] == "optionset-binding"]
        assert len(errs) == 1
        assert errs[0]["component"] == "cwx_missingset"

    def test_declared_global_binding_is_ok(self, tmp_path):
        p = tmp_path / "good_os.zip"
        _make_pkg(p, _sol(),
                  _cust(optionsets='<optionset Name="cwx_set"/>',
                        entities=_attr_global_optionset("cwx_set")))
        report = sv.validate_solution(p)
        assert [f for f in report["findings"] if f["check"] == "optionset-binding"] == []

    def test_binding_resolved_against_org(self, tmp_path, backend):
        p = tmp_path / "org_os.zip"
        _make_pkg(p, _sol(), _cust(entities=_attr_global_optionset("cwx_inorg")))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"GlobalOptionSetDefinitions"),
                  json={"value": [{"Name": "cwx_inorg"}]})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "optionset-binding"] == []
