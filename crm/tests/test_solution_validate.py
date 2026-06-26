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

def _make_pkg(path, solution_xml, customizations_xml, content_types=True, workflows=None):
    """Write a minimal solution zip (solution.xml + customizations.xml [+ Content_Types]).

    `workflows` is an optional {member_path: xaml_bytes_or_str} mapping whose keys
    are written verbatim as zip members — callers pass the full member path
    (e.g. "Workflows/MyBpf.xaml") for BPF process XAML.
    """
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("solution.xml", solution_xml)
        zf.writestr("customizations.xml", customizations_xml)
        if content_types:
            zf.writestr("[Content_Types].xml", "<Types/>")
        for name, body in (workflows or {}).items():
            zf.writestr(name, body)


def _sol(roots="", package_version=""):
    attr = f' SolutionPackageVersion="{package_version}"' if package_version else ""
    return (
        '<?xml version="1.0"?>\n'
        f"<ImportExportXml{attr}><SolutionManifest><UniqueName>cwx_test</UniqueName>"
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


# ── #163: BPF stage-GUID collisions in Workflows/*.xaml ───────────────────────

_STAGE_GUID = "33333333-3333-3333-3333-333333333333"
_NEXT_STAGE_GUID = "44444444-4444-4444-4444-444444444444"


def _xaml(*, stage_id=None, next_stage_id=None):
    """Minimal BPF process XAML with the `x` namespace declared, like a real export."""
    body = ['<Activity xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">']
    if stage_id is not None:
        body.append(f'<x:String x:Key="StageId">{stage_id}</x:String>')
    if next_stage_id is not None:
        body.append(f'<x:String x:Key="NextStageId">{next_stage_id}</x:String>')
    body.append("</Activity>")
    return "".join(body)


class TestXamlStageCollisions:
    def test_colliding_stage_id_is_error(self, tmp_path, backend):
        p = tmp_path / "bpf_collide.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(stage_id=_STAGE_GUID)})
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"),
                  json={"value": [{"processstageid": _STAGE_GUID}]})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _STAGE_GUID in errs[0]["message"]
        assert errs[0]["severity"] == "error"
        assert "guid-collision" in report["checks_run"]
        assert report["valid"] is False

    def test_colliding_next_stage_id_is_error(self, tmp_path, backend):
        # The only colliding GUID lives in a NextStageId element.
        p = tmp_path / "bpf_next.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(next_stage_id=_NEXT_STAGE_GUID)})
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"),
                  json={"value": [{"processstageid": _NEXT_STAGE_GUID}]})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _NEXT_STAGE_GUID in errs[0]["message"]

    def test_no_stage_collision_is_ok(self, tmp_path, backend):
        p = tmp_path / "bpf_nocollide.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(stage_id=_STAGE_GUID)})
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"), json={"value": []})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "guid-collision"] == []
        assert report["valid"] is True

    def test_stage_collisions_skipped_without_backend(self, tmp_path):
        p = tmp_path / "bpf_offline.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(stage_id=_STAGE_GUID)})
        report = sv.validate_solution(p)
        assert "guid-collision" not in report["checks_run"]
        assert [f for f in report["findings"] if f["check"] == "guid-collision"] == []

    def test_malformed_xaml_member_does_not_crash(self, tmp_path, backend):
        # A non-parseable XAML member must degrade, never raise (CLI only catches
        # D365Error) — that is the primary guarantee. The member is unterminated
        # XML overall (an ElementTree parse would throw) yet still embeds a well-
        # formed, recoverable <x:String x:Key="StageId">{guid}</x:String>; the
        # regex/best-effort scan finds that GUID despite the broken neighbour,
        # which is the whole reason a regex is used here over ElementTree.
        p = tmp_path / "bpf_malformed.zip"
        body = (
            '<Activity xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">'
            f'<x:String x:Key="StageId">{_STAGE_GUID}</x:String>'
            '<x:String x:Key=unterminated'  # malformed: unquoted attr, no close
        )
        _make_pkg(p, _sol(), _cust(), workflows={"Workflows/Bad.xaml": body})
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"),
                  json={"value": [{"processstageid": _STAGE_GUID}]})
            report = sv.validate_solution(p, backend=backend)  # must not raise
        assert isinstance(report["findings"], list)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _STAGE_GUID in errs[0]["message"]

    def test_stage_id_with_trailing_attribute_is_extracted(self, tmp_path, backend):
        # A real export can carry xml:space="preserve" (or any attr) AFTER x:Key on
        # the value element. The scan must still pull the GUID and probe the org.
        p = tmp_path / "bpf_trailing_attr.zip"
        body = (
            '<Activity xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">'
            f'<x:String x:Key="StageId" xml:space="preserve">{_STAGE_GUID}</x:String>'
            "</Activity>"
        )
        _make_pkg(p, _sol(), _cust(), workflows={"Workflows/MyBpf.xaml": body})
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"),
                  json={"value": [{"processstageid": _STAGE_GUID}]})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _STAGE_GUID in errs[0]["message"]

    def test_same_guid_in_stage_and_next_stage_dedups_to_one_finding(self, tmp_path, backend):
        # The same GUID appearing in both a StageId and a NextStageId element must
        # be probed once and yield exactly ONE finding, not one per occurrence.
        p = tmp_path / "bpf_dedup.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml":
                             _xaml(stage_id=_STAGE_GUID, next_stage_id=_STAGE_GUID)})
        with requests_mock.Mocker() as m:
            stages = m.get(re.compile(r"processstages"),
                           json={"value": [{"processstageid": _STAGE_GUID}]})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _STAGE_GUID in errs[0]["message"]
        assert stages.call_count == 1  # probed once, not per occurrence

    def test_unreadable_xaml_member_does_not_crash(self, tmp_path, backend, monkeypatch):
        # An unreadable member (RuntimeError on read) must not escape as a non-
        # D365Error exception; degrade to a finding or skip it.
        p = tmp_path / "bpf_unreadable.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/Enc.xaml": _xaml(stage_id=_STAGE_GUID)})
        orig_read = zipfile.ZipFile.read

        def boom(self, name, *a, **k):
            if str(name).endswith(".xaml"):
                raise RuntimeError("That compression method is not supported")
            return orig_read(self, name, *a, **k)

        monkeypatch.setattr(zipfile.ZipFile, "read", boom)
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"), json={"value": []})
            report = sv.validate_solution(p, backend=backend)  # must not raise
        assert isinstance(report["findings"], list)

    def test_corrupt_member_degrades_and_scan_continues(self, tmp_path, backend, monkeypatch):
        # A single corrupt member (bad CRC -> BadZipFile on read) degrades to a
        # 'package' finding without aborting the whole scan: a second, good
        # member's stage GUID is still probed and its collision reported.
        p = tmp_path / "bpf_corrupt.zip"
        good_guid = _STAGE_GUID
        _make_pkg(p, _sol(), _cust(), workflows={
            "Workflows/Bad.xaml": _xaml(stage_id="99999999-9999-9999-9999-999999999999"),
            "Workflows/Good.xaml": _xaml(stage_id=good_guid),
        })
        orig_read = zipfile.ZipFile.read

        def boom(self, name, *a, **k):
            if str(name).endswith("Bad.xaml"):
                raise zipfile.BadZipFile("Bad CRC-32 for file Workflows/Bad.xaml")
            return orig_read(self, name, *a, **k)

        monkeypatch.setattr(zipfile.ZipFile, "read", boom)
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"),
                  json={"value": [{"processstageid": good_guid}]})
            report = sv.validate_solution(p, backend=backend)  # must not raise
        assert any(f["check"] == "package" and "Bad.xaml" in f["message"]
                   for f in report["findings"])
        assert any(f["check"] == "guid-collision" and good_guid in f["message"]
                   for f in report["findings"])

    def test_reopen_failure_degrades_to_package_finding(self, tmp_path, backend, monkeypatch):
        # _load opens the zip first and succeeds; if re-opening it to scan
        # Workflows/*.xaml then fails (BadZipFile/OSError), the scan must emit a
        # 'package' finding rather than silently skip — and never raise.
        p = tmp_path / "bpf_reopen.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(stage_id=_STAGE_GUID)})
        real_zipfile = zipfile.ZipFile
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] >= 2:  # _load's open succeeds; the re-read open fails
                raise zipfile.BadZipFile("file changed under us")
            return real_zipfile(*a, **k)

        monkeypatch.setattr(sv.zipfile, "ZipFile", flaky)
        report = sv.validate_solution(p, backend=backend)  # must not raise
        assert any(f["check"] == "package" and "Workflows/*.xaml" in f["message"]
                   for f in report["findings"])

    def test_utf16_encoded_member_is_decoded_and_probed(self, tmp_path, backend):
        # D365 process XAML is often UTF-16 (BOM-prefixed). The scan must decode
        # by BOM, not blindly as UTF-8, or the StageId regex misses the GUID.
        p = tmp_path / "bpf_utf16.zip"
        xaml_bytes = _xaml(stage_id=_STAGE_GUID).encode("utf-16")  # adds a BOM
        _make_pkg(p, _sol(), _cust(), workflows={"Workflows/Utf16.xaml": xaml_bytes})
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"),
                  json={"value": [{"processstageid": _STAGE_GUID}]})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "guid-collision"]
        assert len(errs) == 1
        assert _STAGE_GUID in errs[0]["message"]

    def test_non_guid_capture_is_not_probed(self, tmp_path, backend):
        # A malformed/unexpected StageId value must never reach the OData $filter
        # (would 400 / allow injection); it is skipped, not probed.
        p = tmp_path / "bpf_badval.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/Bad.xaml": _xaml(stage_id="not-a-guid' or 1 eq 1")})
        with requests_mock.Mocker() as m:
            stages = m.get(re.compile(r"processstages"), json={"value": []})
            report = sv.validate_solution(p, backend=backend)
        assert not stages.called  # malformed value never hit the org
        assert [f for f in report["findings"] if f["check"] == "guid-collision"] == []

    def test_stage_collision_under_dry_run_forces_real_get(self, tmp_path):
        # Mirror TestDryRunForcesRealReads: the probe must hit the network even
        # under --dry-run, and the dry_run flag must be restored after.
        dry_backend = D365Backend(
            ConnectionProfile(name="t", url="https://crm.contoso.local/contoso",
                              domain="C", username="u", api_version="v9.2", verify_ssl=False),
            password="pw", dry_run=True)
        p = tmp_path / "bpf_dry.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(stage_id=_STAGE_GUID)})
        with requests_mock.Mocker() as m:
            stages = m.get(re.compile(r"processstages"),
                           json={"value": [{"processstageid": _STAGE_GUID}]})
            report = sv.validate_solution(p, backend=dry_backend)
        assert stages.called  # a real GET fired despite dry_run
        assert any(f["check"] == "guid-collision" for f in report["findings"])

    def test_cli_against_org_detects_stage_collision(self, tmp_path, backend, monkeypatch):
        p = tmp_path / "bpf_cli.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(stage_id=_STAGE_GUID)})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"processstages"),
                  json={"value": [{"processstageid": _STAGE_GUID}]})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert any(f["check"] == "guid-collision" and _STAGE_GUID in f["message"]
                   for f in data["data"]["findings"])


# ── #269: skip only the collision checks for round-trip update-imports ────────


class TestCheckCollisionsFlag:
    """#269: a round-trip update-import (e.g. a ribbon edit: export→mutate→re-import)
    re-imports the entity's *existing* forms/views, so the GUID-collision checks
    fire false positives. `check_collisions=False` must skip ONLY those checks
    (`_check_org_collisions` + `_check_xaml_stage_collisions`) while still running
    `webresource-ref` and `optionset-binding`. Default (`True`) is unchanged."""

    def test_collisions_disabled_skips_guid_checks_but_keeps_others(self, tmp_path, backend):
        # Package carries all three: a form whose GUID collides on the org (would
        # normally be a guid-collision error), an unresolved ribbon web-resource
        # ref, and an undeclared global option-set binding.
        p = tmp_path / "roundtrip.zip"
        _make_pkg(p, _sol(), _cust(
            entities=_form(_FORM_GUID) + _ribbon("cwx_/missing.js")
            + _attr_global_optionset("cwx_missingset"),
        ))
        with requests_mock.Mocker() as m:
            sysforms = m.get(re.compile(r"systemforms"),
                             json={"value": [{"formid": _FORM_GUID}]})
            m.get(re.compile(r"savedqueries"), json={"value": []})
            m.get(re.compile(r"webresourceset"), json={"value": []})
            m.get(re.compile(r"GlobalOptionSetDefinitions"), json={"value": []})
            report = sv.validate_solution(p, backend=backend, check_collisions=False)
        # collision check did not run and never probed the org
        assert "guid-collision" not in report["checks_run"]
        assert [f for f in report["findings"] if f["check"] == "guid-collision"] == []
        assert not sysforms.called
        # the other backend-dependent checks still ran and still flag problems
        assert "webresource-ref" in report["checks_run"]
        assert "optionset-binding" in report["checks_run"]
        assert any(f["check"] == "webresource-ref" for f in report["findings"])
        assert any(f["check"] == "optionset-binding" for f in report["findings"])

    def test_collisions_disabled_skips_xaml_stage_check(self, tmp_path, backend):
        # The XAML-stage collision check is part of the same guid-collision family
        # and must also be skipped (and not probe processstages) when disabled.
        p = tmp_path / "roundtrip_bpf.zip"
        _make_pkg(p, _sol(), _cust(),
                  workflows={"Workflows/MyBpf.xaml": _xaml(stage_id=_STAGE_GUID)})
        with requests_mock.Mocker() as m:
            stages = m.get(re.compile(r"processstages"),
                           json={"value": [{"processstageid": _STAGE_GUID}]})
            report = sv.validate_solution(p, backend=backend, check_collisions=False)
        assert not stages.called
        assert "guid-collision" not in report["checks_run"]
        assert [f for f in report["findings"] if f["check"] == "guid-collision"] == []

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


# ── #325: package-version compatibility (--against-org) ───────────────────────


class TestPackageVersionCompatibility:
    """#325: a solution exported from a newer Dataverse version (even a newer
    *minor*) cannot be imported into an older org — import fails with 0x80048068.
    On the --against-org path, compare the package's SolutionPackageVersion (on the
    ImportExportXml root) against the org version from RetrieveVersion()."""

    def test_newer_package_than_org_is_error(self, tmp_path, backend):
        p = tmp_path / "newer.zip"
        _make_pkg(p, _sol(package_version="9.2.0.0"), _cust())
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"RetrieveVersion"), json={"Version": "9.1.0.643"})
            report = sv.validate_solution(p, backend=backend)
        errs = [f for f in report["findings"] if f["check"] == "package-version"]
        assert len(errs) == 1
        assert errs[0]["severity"] == "error"
        assert "9.2.0.0" in errs[0]["message"] and "9.1.0.643" in errs[0]["message"]
        assert "0x80048068" in errs[0]["message"]
        assert "package-version" in report["checks_run"]
        assert report["valid"] is False

    def test_older_or_equal_package_is_ok(self, tmp_path, backend):
        # Package minor <= org minor → no version finding; valid unaffected. The
        # check still ran (appears in checks_run).
        p = tmp_path / "older.zip"
        _make_pkg(p, _sol(package_version="9.1.0.0"), _cust())
        with requests_mock.Mocker() as m:
            ver = m.get(re.compile(r"RetrieveVersion"), json={"Version": "9.2.0.643"})
            report = sv.validate_solution(p, backend=backend)
        assert ver.called
        assert [f for f in report["findings"] if f["check"] == "package-version"] == []
        assert "package-version" in report["checks_run"]
        assert report["valid"] is True

    def test_equal_minor_ignores_build_granularity(self, tmp_path, backend):
        # Same major.minor: a higher *build* in the package must NOT false-positive —
        # the org is compared only to the package's granularity (major.minor here).
        p = tmp_path / "samepkg.zip"
        _make_pkg(p, _sol(package_version="9.2"), _cust())
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"RetrieveVersion"), json={"Version": "9.2.0.100"})
            report = sv.validate_solution(p, backend=backend)
        assert [f for f in report["findings"] if f["check"] == "package-version"] == []
        assert report["valid"] is True

    def test_missing_package_version_skips_without_network(self, tmp_path, backend):
        # No SolutionPackageVersion attribute → skip silently, no RetrieveVersion
        # call, no finding, valid unaffected. (Also proves the existing online
        # tests, which omit the attribute, never hit an unmocked RetrieveVersion.)
        p = tmp_path / "noversion.zip"
        _make_pkg(p, _sol(), _cust())
        with requests_mock.Mocker() as m:
            ver = m.get(re.compile(r"RetrieveVersion"), json={"Version": "9.1.0.0"})
            report = sv.validate_solution(p, backend=backend)
        assert not ver.called
        assert [f for f in report["findings"] if f["check"] == "package-version"] == []
        assert report["valid"] is True
        assert "package-version" in report["checks_run"]

    def test_unparseable_package_version_skips(self, tmp_path, backend):
        # A non-numeric SolutionPackageVersion is unparseable → skip, never crash,
        # never flip valid, and no network probe.
        p = tmp_path / "badversion.zip"
        _make_pkg(p, _sol(package_version="not.a.version"), _cust())
        with requests_mock.Mocker() as m:
            ver = m.get(re.compile(r"RetrieveVersion"), json={"Version": "9.1.0.0"})
            report = sv.validate_solution(p, backend=backend)
        assert not ver.called
        assert [f for f in report["findings"] if f["check"] == "package-version"] == []
        assert report["valid"] is True

    def test_retrieveversion_failure_is_warning_not_error(self, tmp_path, backend):
        # A failed RetrieveVersion() must degrade to a warning (not crash, not flip
        # valid) — an indeterminate comparison is never a false rejection.
        p = tmp_path / "verfail.zip"
        _make_pkg(p, _sol(package_version="9.2.0.0"), _cust())
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"RetrieveVersion"), status_code=500)
            report = sv.validate_solution(p, backend=backend)
        vers = [f for f in report["findings"] if f["check"] == "package-version"]
        assert len(vers) == 1
        assert vers[0]["severity"] == "warning"
        assert report["valid"] is True
        assert "package-version" in report["checks_run"]

    def test_empty_org_version_is_warning_not_error(self, tmp_path, backend):
        # RetrieveVersion() returns no usable version → warning, valid unaffected.
        p = tmp_path / "emptyver.zip"
        _make_pkg(p, _sol(package_version="9.2.0.0"), _cust())
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"RetrieveVersion"), json={})
            report = sv.validate_solution(p, backend=backend)
        vers = [f for f in report["findings"] if f["check"] == "package-version"]
        assert len(vers) == 1
        assert vers[0]["severity"] == "warning"
        assert report["valid"] is True

    def test_offline_path_skips_version_check(self, tmp_path):
        # No --against-org (no backend): the version check does not run and there
        # is no network call. Offline behavior is unchanged.
        p = tmp_path / "offline.zip"
        _make_pkg(p, _sol(package_version="9.2.0.0"), _cust())
        report = sv.validate_solution(p)
        assert "package-version" not in report["checks_run"]
        assert [f for f in report["findings"] if f["check"] == "package-version"] == []
        assert report["valid"] is True

    def test_version_check_runs_even_if_customizations_unparseable(self, tmp_path, backend):
        # The version check needs only solution.xml; a broken customizations.xml
        # (its own package error) must not suppress the version-ceiling finding.
        p = tmp_path / "badcust.zip"
        _make_pkg(p, _sol(package_version="9.2.0.0"), "<not-well-formed")
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"RetrieveVersion"), json={"Version": "9.1.0.643"})
            report = sv.validate_solution(p, backend=backend)
        vers = [f for f in report["findings"] if f["check"] == "package-version"]
        assert len(vers) == 1 and vers[0]["severity"] == "error"
        assert "package-version" in report["checks_run"]
        assert report["valid"] is False

    def test_cli_against_org_detects_version_ceiling(self, tmp_path, backend, monkeypatch):
        p = tmp_path / "cli_ver.zip"
        _make_pkg(p, _sol(package_version="9.2.0.0"), _cust())
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        import json
        with requests_mock.Mocker() as m:
            m.get(re.compile(r"RetrieveVersion"), json={"Version": "9.1.0.643"})
            result = CliRunner().invoke(
                cli, ["--json", "solution", "validate", str(p), "--against-org"])
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert any(f["check"] == "package-version" and "0x80048068" in f["message"]
                   for f in data["data"]["findings"])
