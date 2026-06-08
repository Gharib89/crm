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

    def test_unscanned_rootcomponent_type_is_not_flagged(self, tmp_path):
        # A real solution roots many component types (workflows type 29, plug-in
        # assemblies type 91, …) that have no customizations node we scan. The
        # reverse parity direction must NOT flag those as "no definition".
        p = tmp_path / "workflow_root.zip"
        _make_pkg(p, _sol('<RootComponent type="29" id="{aaaaaaaa-0000-0000-0000-000000000000}"/>'
                          '<RootComponent type="91" id="{bbbbbbbb-0000-0000-0000-000000000000}"/>'),
                  _cust())
        report = sv.validate_solution(p)
        assert [f for f in report["findings"] if f["check"] == "root-parity"] == []
        assert report["valid"] is True


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


# ── Task 5: org GUID collisions (--against-org) ───────────────────────────────

_FORM_GUID = "22222222-2222-2222-2222-222222222222"


def _form(guid):
    return f'<Entity><FormXml><forms><systemform><formid>{guid}</formid></systemform></forms></FormXml></Entity>'


class TestOrgCollisions:
    def test_colliding_formid_is_error(self, tmp_path, backend):
        p = tmp_path / "collide.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _FORM_GUID in errs[0]["message"]
        assert "guid-collision" in report["checks_run"]

    def test_no_collision_is_ok(self, tmp_path, backend):
        p = tmp_path / "nocollide.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": []})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "guid-collision"] == []

    def test_collisions_skipped_without_backend(self, tmp_path):
        p = tmp_path / "offline.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        report = sv.validate_solution(p)
        assert "guid-collision" not in report["checks_run"]


# ── Task 6: CLI wiring ────────────────────────────────────────────────────────

class TestValidateCli:
    def test_good_package_exit_zero(self, tmp_path):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_s"/>'),
                  _cust(optionsets='<optionset Name="cwx_s"/>'))
        result = CliRunner().invoke(cli, ["--json", "solution", "validate", str(p)])
        assert result.exit_code == 0, result.output
        import json
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["valid"] is True

    def test_parity_problem_exit_one(self, tmp_path):
        p = tmp_path / "bad.zip"
        _make_pkg(p, _sol(), _cust(optionsets='<optionset Name="cwx_orphan"/>'))
        result = CliRunner().invoke(cli, ["--json", "solution", "validate", str(p)])
        assert result.exit_code == 1, result.output
        import json
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["data"]["valid"] is False
        assert "error" in data and data["error"]

    def test_against_org_uses_backend(self, tmp_path, backend, monkeypatch):
        p = tmp_path / "collide.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert any(f["check"] == "guid-collision" for f in data["data"]["findings"])


# ── Task 7: acceptance (issue #141) ───────────────────────────────────────────

class TestAcceptance:
    def test_all_three_classes_in_one_pass(self, tmp_path, backend, monkeypatch):
        """One `validate --against-org` pass reports class #1 (optionset not in
        RootComponents), #3 (dashboard not in RootComponents), and #2 (colliding
        formid in org); exit non-zero."""
        p = tmp_path / "all_three.zip"
        _make_pkg(
            p,
            _sol(),  # empty RootComponents → optionset + dashboard are orphans
            _cust(
                optionsets='<optionset Name="cwx_slatier"/>',
                dashboards=f"<Dashboard><FormId>{_DASH_GUID}</FormId></Dashboard>",
                entities=_form(_FORM_GUID),
            ),
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        checks = {f["check"] for f in data["data"]["findings"]}
        assert "root-parity" in checks      # classes #1 + #3
        assert "guid-collision" in checks    # class #2
        parity = [f for f in data["data"]["findings"] if f["check"] == "root-parity"]
        assert {"cwx_slatier", _DASH_GUID} <= {f["component"] for f in parity}

    def test_good_package_against_org_exit_zero(self, tmp_path, backend, monkeypatch):
        p = tmp_path / "good.zip"
        _make_pkg(p, _sol('<RootComponent type="9" schemaName="cwx_s"/>'),
                  _cust(optionsets='<optionset Name="cwx_s"/>'))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"systemforms"), json={"value": []})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["valid"] is True


# ── Copilot round 1: robustness fixes ─────────────────────────────────────────

class TestDryRunForcesRealReads:
    """--against-org probes are read-only and must force a real GET even under
    --dry-run; otherwise the dry-run preview response (no 'value' key) hides
    real collisions / fakes missing web resources & option sets (#149 review)."""

    def test_org_collision_detected_under_dry_run(self, tmp_path):
        dry_backend = D365Backend(
            ConnectionProfile(name="t", url="https://crm.contoso.local/contoso",
                              domain="C", username="u", api_version="v9.2", verify_ssl=False),
            password="pw", dry_run=True)
        p = tmp_path / "collide.zip"
        _make_pkg(p, _sol(), _cust(entities=_form(_FORM_GUID)))
        with requests_mock.Mocker() as m:
            sysforms = m.get(re.compile(r"systemforms"), json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            report = sv.validate_solution(p, backend=dry_backend)
        assert sysforms.called  # a real GET fired despite dry_run
        assert any(f["check"] == "guid-collision" for f in report["findings"])
        assert dry_backend.dry_run is True  # flag restored after the probe


def test_unreadable_member_degrades_to_package_finding(tmp_path, monkeypatch):
    """A member that can't be read (encrypted -> RuntimeError, unsupported
    compression -> NotImplementedError, oversized -> LargeZipFile) must produce
    a 'package' finding, not crash the CLI (which only catches D365Error)."""
    p = tmp_path / "weird.zip"
    _make_pkg(p, _sol(), _cust())
    orig_read = zipfile.ZipFile.read

    def boom(self, name, *a, **k):
        if name == "solution.xml":
            raise RuntimeError("That compression method is not supported")
        return orig_read(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "read", boom)
    report = sv.validate_solution(p)  # must not raise
    assert report["valid"] is False
    assert any(f["check"] == "package" for f in report["findings"])
