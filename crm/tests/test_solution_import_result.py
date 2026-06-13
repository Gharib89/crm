"""Unit tests for ImportJob.data parsing + the import-result envelope (#70)."""
# pyright: basic

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol
from crm.utils.d365_backend import ConnectionProfile, D365Backend

_JOB_ID = "33333333-3333-3333-3333-333333333333"


# A real op-9-1 ImportJob.data document (MS docs "Work with solutions" sample):
# a managed solution carrying a single global option set, everything succeeded.
_DATA_SUCCESS = """<importexportxml start="634224017519682730" stop="634224017609764033" progress="80" processed="true">
 <solutionManifests>
  <solutionManifest languagecode="1033" id="samplesolutionforImport" LocalizedName="Sample Solution for Import" processed="true">
   <UniqueName>samplesolutionforImport</UniqueName>
   <Version>1.0</Version>
   <Managed>1</Managed>
   <results />
   <result result="success" errorcode="0" errortext="" datetime="20:49:12.08" datetimeticks="634224269520845122" />
  </solutionManifest>
 </solutionManifests>
 <entities />
 <optionSets>
  <optionSet id="sample_tempsampleglobaloptionsetname" LocalizedName="Example Option Set" Description="" processed="true">
   <result result="success" errorcode="0" errortext="" datetime="20:49:16.10" datetimeticks="634224269561025400" />
  </optionSet>
 </optionSets>
 <rootComponents>
  <rootComponent processed="true">
   <result result="success" errorcode="0" errortext="" datetime="20:49:20.83" datetimeticks="634224269608387238" />
  </rootComponent>
 </rootComponents>
</importexportxml>"""


def test_parse_success_returns_overall_and_components():
    env = sol.parse_import_job_data(_DATA_SUCCESS)
    assert env["result"] == "success"
    assert env["solution"] == "samplesolutionforImport"
    opt = next(c for c in env["components"] if c["type"] == "optionSet")
    assert opt["name"] == "Example Option Set"
    assert opt["result"] == "success"


# A manifest that reports overall success while one entity component failed —
# the partial failure the async statuscode==30 path would otherwise mask.
_DATA_PARTIAL = """<importexportxml progress="100" processed="true">
 <solutionManifests>
  <solutionManifest LocalizedName="Partial Solution" processed="true">
   <UniqueName>partialsolution</UniqueName>
   <result result="success" errorcode="0" errortext="" />
  </solutionManifest>
 </solutionManifests>
 <entities>
  <entity LocalizedName="Account" id="account" processed="true">
   <result result="failure" errorcode="0x80044182" errortext="Attribute cwx_foo is missing." />
  </entity>
 </entities>
 <optionSets>
  <optionSet LocalizedName="Good Set" processed="true">
   <result result="success" errorcode="0" errortext="" />
  </optionSet>
 </optionSets>
</importexportxml>"""


def test_parse_captures_failed_component_detail():
    env = sol.parse_import_job_data(_DATA_PARTIAL)
    assert env["result"] == "success"  # manifest says success...
    failed = next(c for c in env["components"] if c["result"] == "failure")
    assert failed["type"] == "entity"
    assert failed["name"] == "Account"
    assert failed["errorcode"] == "0x80044182"
    assert failed["errortext"] == "Attribute cwx_foo is missing."
    # a success component carries no error detail
    good = next(c for c in env["components"] if c["name"] == "Good Set")
    assert "errorcode" not in good and "errortext" not in good


def test_parse_anonymous_component_name_falls_back_to_type():
    # <rootComponent> carries no LocalizedName/name/id/UniqueName — name must
    # still be a non-null label so the {name,type,result} contract holds.
    env = sol.parse_import_job_data(_DATA_SUCCESS)
    rc = next(c for c in env["components"] if c["type"] == "rootComponent")
    assert rc["name"] == "rootComponent"


def test_parse_overall_failure():
    xml = (
        '<importexportxml><solutionManifests>'
        '<solutionManifest><UniqueName>s</UniqueName>'
        '<result result="failure" errorcode="0x8004" errortext="boom" />'
        '</solutionManifest></solutionManifests></importexportxml>'
    )
    env = sol.parse_import_job_data(xml)
    assert env["result"] == "failure"


def test_parse_unparseable_raises():
    from crm.utils.d365_backend import D365Error
    with pytest.raises(D365Error, match="empty"):
        sol.parse_import_job_data("   ")
    with pytest.raises(D365Error, match="parse"):
        sol.parse_import_job_data("<not-closed>")


def test_import_result_reports_partial_failure_warning(backend):
    with requests_mock.Mocker() as m:
        m.get(
            backend.url_for(f"importjobs({_JOB_ID})"),
            json={"data": _DATA_PARTIAL, "solutionname": "partialsolution",
                  "progress": 100.0, "completedon": "2026-06-05T00:00:00Z"},
        )
        out = sol.import_result(backend, _JOB_ID)
    assert out["import_job_id"] == _JOB_ID
    assert out["solution"] == "partialsolution"
    assert out["result"] == "success"            # async said succeeded...
    assert out["warnings"]                        # ...but a component failed
    assert any("Account" in w for w in out["warnings"])


def test_import_result_formatted_attaches_report_verbatim(backend):
    report = "<xml>Excel-format report</xml>"
    with requests_mock.Mocker() as m:
        m.get(backend.url_for(f"importjobs({_JOB_ID})"),
              json={"data": _DATA_SUCCESS, "solutionname": "s", "progress": 100.0})
        m.get(backend.url_for(f"RetrieveFormattedImportJobResults(ImportJobId={_JOB_ID})"),
              json={"FormattedResults": report})
        out = sol.import_result(backend, _JOB_ID, formatted=True)
    assert out["formatted_results"] == report


def test_import_result_missing_data_warns_not_raises(backend):
    # A job row with no data column: best-effort parsing degrades to a warning
    # rather than erroring — same contract as import_solution.
    with requests_mock.Mocker() as m:
        m.get(backend.url_for(f"importjobs({_JOB_ID})"),
              json={"solutionname": "s", "progress": 100.0})  # no data
        out = sol.import_result(backend, _JOB_ID)
    assert out["import_job_id"] == _JOB_ID
    assert out["solution"] == "s"
    assert "result" not in out
    assert any("not verified" in w for w in out["warnings"])


def test_import_result_without_formatted_omits_report(backend):
    # No mock for RetrieveFormattedImportJobResults — a call would 404 (NoMockAddress).
    with requests_mock.Mocker() as m:
        m.get(backend.url_for(f"importjobs({_JOB_ID})"),
              json={"data": _DATA_SUCCESS, "solutionname": "s", "progress": 100.0})
        out = sol.import_result(backend, _JOB_ID)
    assert "formatted_results" not in out
    assert "warnings" not in out                  # clean success → no warnings


def test_import_solution_surfaces_partial_failure(backend, tmp_path, no_sleep):
    zip_path = tmp_path / "in.zip"
    zip_path.write_bytes(b"PK\x03\x04stub")
    with requests_mock.Mocker() as m:
        m.post(backend.url_for("ImportSolutionAsync"),
               json={"AsyncOperationId": "55555555-5555-5555-5555-555555555555"})
        m.get(re.compile(r"asyncoperations"),
              json={"statecode": 3, "statuscode": 30, "message": "Done"})
        m.get(re.compile(r"importjobs"),
              json={"progress": 100.0, "startedon": "2026-06-05T00:00:00Z",
                    "completedon": "2026-06-05T00:01:00Z", "data": _DATA_PARTIAL})
        info = sol.import_solution(backend, zip_path, quiet=True)
    assert info["status"] == "succeeded"          # async statuscode 30
    assert info["result"] == "success"            # manifest-level
    assert info["warnings"]                        # ...partial failure no longer hidden
    assert any("Account" in w for w in info["warnings"])


def test_import_solution_warns_when_data_missing(backend, tmp_path, no_sleep):
    # No data column on the final read: per-component results can't be verified,
    # so the import must say so rather than silently omit them (an absent
    # result/components could be mistaken for "checked and clean").
    zip_path = tmp_path / "in.zip"
    zip_path.write_bytes(b"PK\x03\x04stub")
    with requests_mock.Mocker() as m:
        m.post(backend.url_for("ImportSolutionAsync"),
               json={"AsyncOperationId": "55555555-5555-5555-5555-555555555555"})
        m.get(re.compile(r"asyncoperations"),
              json={"statecode": 3, "statuscode": 30, "message": "Done"})
        m.get(re.compile(r"importjobs"), json={"progress": 100.0})  # no data
        info = sol.import_solution(backend, zip_path, quiet=True)
    assert info["status"] == "succeeded"
    assert "result" not in info
    assert info["warnings"]
    assert any("not verified" in w for w in info["warnings"])


# ── managed/unmanaged sniff (#91) ────────────────────────────────────────


def _make_solution_zip(path, managed_flag):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "solution.xml",
            f"<ImportExportXml><SolutionManifest>"
            f"<UniqueName>s</UniqueName><Managed>{managed_flag}</Managed>"
            f"</SolutionManifest></ImportExportXml>",
        )


_ASYNC_OP_ID = "55555555-5555-5555-5555-555555555555"


def test_import_solution_managed_field_false_on_valid_unmanaged_zip(backend, tmp_path, no_sleep):
    """Real import with a valid unmanaged zip → managed is False."""
    zip_path = tmp_path / "unmanaged.zip"
    _make_solution_zip(zip_path, "0")
    with requests_mock.Mocker() as m:
        m.post(backend.url_for("ImportSolutionAsync"),
               json={"AsyncOperationId": _ASYNC_OP_ID})
        m.get(re.compile(r"asyncoperations"),
              json={"statecode": 3, "statuscode": 30, "message": "Done"})
        m.get(re.compile(r"importjobs"),
              json={"progress": 100.0, "startedon": "2026-06-05T00:00:00Z",
                    "completedon": "2026-06-05T00:01:00Z", "data": _DATA_PARTIAL})
        info = sol.import_solution(backend, zip_path, quiet=True)
    assert info["managed"] is False


def test_import_solution_managed_field_none_on_garbage_zip(backend, tmp_path, no_sleep):
    """Real import with a garbage non-zip stub → managed is None."""
    zip_path = tmp_path / "garbage.zip"
    zip_path.write_bytes(b"PK\x03\x04stub")
    with requests_mock.Mocker() as m:
        m.post(backend.url_for("ImportSolutionAsync"),
               json={"AsyncOperationId": _ASYNC_OP_ID})
        m.get(re.compile(r"asyncoperations"),
              json={"statecode": 3, "statuscode": 30, "message": "Done"})
        m.get(re.compile(r"importjobs"),
              json={"progress": 100.0, "startedon": "2026-06-05T00:00:00Z",
                    "completedon": "2026-06-05T00:01:00Z", "data": _DATA_PARTIAL})
        info = sol.import_solution(backend, zip_path, quiet=True)
    assert info["managed"] is None


def test_import_solution_dry_run_includes_managed_and_dry_run_sentinel(tmp_path):
    """Dry-run: managed False present AND _dry_run sentinel preserved."""
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso", domain="C",
        username="u", api_version="v9.2", verify_ssl=False,
    )
    dry_backend = D365Backend(profile, password="pw", dry_run=True)
    zip_path = tmp_path / "unmanaged.zip"
    _make_solution_zip(zip_path, "0")
    out = sol.import_solution(dry_backend, zip_path, quiet=True)
    assert "_dry_run" in out
    assert out["managed"] is False


# ── command layer ────────────────────────────────────────────────────────


def _save_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    profile = ConnectionProfile(
        name="p", url="https://crm.contoso.local/contoso", domain="C", username="alice",
    )
    session_mod.save_profile(profile)
    session_mod.save_profile_secret_plaintext("p", "pw")
    state = session_mod.load_session("default")
    state["active_profile"] = "p"
    session_mod.save_session(state, "default")


def test_import_result_command_surfaces_warnings_and_formatted(monkeypatch, tmp_path):
    _save_profile(monkeypatch, tmp_path)
    captured = {}

    def fake_import_result(_backend, job_id, *, formatted=False):
        captured["job_id"] = job_id
        captured["formatted"] = formatted
        return {"import_job_id": job_id, "result": "success",
                "warnings": ["entity 'Account' import result is 'failure'."]}

    monkeypatch.setattr(sol, "import_result", fake_import_result)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "p", "solution", "import-result", _JOB_ID, "--formatted"],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is True
    assert env["meta"]["warnings"]                 # lifted into meta.warnings
    assert "warnings" not in env["data"]           # not duplicated in data
    assert captured == {"job_id": _JOB_ID, "formatted": True}


def test_sniff_managed_never_raises_on_unexpected_error(tmp_path, monkeypatch):
    # zf.read can raise NotImplementedError (unsupported compression) /
    # RuntimeError (encrypted) — neither is BadZipFile/OSError. The advisory
    # sniff must still degrade to None, never crash the import.
    zip_path = tmp_path / "in.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("solution.xml", "<ImportExportXml/>")

    class _Boom(zipfile.ZipFile):
        def read(self, *a, **k):  # type: ignore[override]
            raise NotImplementedError("That compression method is not supported")

    monkeypatch.setattr(zipfile, "ZipFile", _Boom)
    assert sol._sniff_solution_managed(str(zip_path)) is None


def test_sniff_managed_returns_true_for_managed_zip(tmp_path):
    # <Managed>1</Managed> branch was not previously covered.
    zip_path = tmp_path / "managed.zip"
    _make_solution_zip(zip_path, "1")
    assert sol._sniff_solution_managed(str(zip_path)) is True


def test_sniff_managed_bails_on_oversized_solution_xml(tmp_path, monkeypatch):
    # Zip-bomb guard: when solution.xml's declared uncompressed size exceeds the
    # cap, bail to None without decompressing. Shrink the cap so a normal
    # manifest trips it (vs. writing a multi-MB file).
    zip_path = tmp_path / "bomb.zip"
    _make_solution_zip(zip_path, "0")
    monkeypatch.setattr(sol, "_MAX_SOLUTION_XML_BYTES", 4)
    assert sol._sniff_solution_managed(str(zip_path)) is None


def test_sniff_managed_accepts_binary_stream(tmp_path):
    # import_solution reads the zip once and sniffs from an in-memory stream, so
    # the helper must accept a file-like object, not only a path.
    zip_path = tmp_path / "stream.zip"
    _make_solution_zip(zip_path, "1")
    assert sol._sniff_solution_managed(io.BytesIO(zip_path.read_bytes())) is True
