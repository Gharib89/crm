"""`crm legacy dependencies` / `crm legacy export-solution` over a mocked CRM 2011 /web endpoint.

The dependency fixtures are real dev-org responses (CRM 2011 UR15) with GUIDs
replaced; the export fixture's envelope is real, its zip a small stand-in.
"""

# pyright: basic
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile

_FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
_URL = "https://crm.contoso.local/contoso"
_ENDPOINT = _URL + "/XRMServices/2011/Organization.svc/web"
_XML = {"Content-Type": "text/xml; charset=utf-8"}
_ACCOUNT = "10000000-0000-0000-0000-000000000001"


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    session_mod.save_profile(
        ConnectionProfile(
            name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011"
        )
    )
    session_mod.save_profile_secret_plaintext("legacy", "pw")
    return tmp_path


def _run(fixture: str, *argv: str, status: int = 200):
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=(_FIXTURES / fixture).read_text(), status_code=status, headers=_XML)
        result = CliRunner().invoke(cli, ["--json", "--profile", "legacy", "legacy", *argv])
    return result, m


def test_dependents_parse_both_sides_of_each_dependency(crm_home):
    result, m = _run(
        "dependents_account.xml", "dependencies", _ACCOUNT, "--component-type", "1",
        "--for", "dependents",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    first = env["data"][0]
    assert first == {
        "dependentcomponentobjectid": "70000000-0000-0000-0000-000000000001",
        "dependentcomponenttype": 92,
        "dependentcomponentparentid": "00000000-0000-0000-0000-000000000000",
        "requiredcomponentobjectid": "70000000-0000-0000-0000-000000000007",
        "requiredcomponenttype": 1,
        "requiredcomponentparentid": "00000000-0000-0000-0000-000000000000",
        "dependencytype": 2,
    }
    assert len(env["data"]) == 2
    assert env["meta"]["for"] == "dependents"
    body = m.request_history[0].body.decode()
    assert "<a:RequestName>RetrieveDependentComponents</a:RequestName>" in body
    assert f">{_ACCOUNT}</b:value>" in body
    assert 'i:type="c:int"' in body


@pytest.mark.parametrize(
    ("for_", "request_name"),
    [
        ("delete", "RetrieveDependenciesForDelete"),
        ("required", "RetrieveRequiredComponents"),
    ],
)
def test_each_for_choice_sends_its_request(crm_home, for_, request_name):
    result, m = _run(
        "required_account.xml", "dependencies", _ACCOUNT, "--component-type", "1", "--for", for_
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"] == []
    body = m.request_history[0].body.decode()
    assert f"<a:RequestName>{request_name}</a:RequestName>" in body


def test_dependencies_default_to_required(crm_home):
    result, m = _run("required_account.xml", "dependencies", _ACCOUNT, "--component-type", "1")
    assert result.exit_code == 0, result.output
    assert "RetrieveRequiredComponents" in m.request_history[0].body.decode()


def test_a_dependency_fault_is_one_error_envelope(crm_home):
    result, _ = _run(
        "fault_entity_not_found.xml", "dependencies", _ACCOUNT, "--component-type", "1",
        status=500,
    )  # fmt: skip
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_export_solution_writes_the_zip_unmanaged(crm_home, tmp_path):
    target = tmp_path / "out" / "sample.zip"
    result, m = _run("export_solution.xml", "export-solution", "contoso_sample", "-o", str(target))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data == {
        "solution": "contoso_sample",
        "path": str(target),
        "bytes": target.stat().st_size,
    }
    with zipfile.ZipFile(target) as archive:
        assert "solution.xml" in archive.namelist()
    body = m.request_history[0].body.decode()
    assert "<a:RequestName>ExportSolution</a:RequestName>" in body
    assert ">contoso_sample</b:value>" in body
    assert 'i:type="c:boolean" xmlns:c="http://www.w3.org/2001/XMLSchema">false<' in body
    # One request: nothing is published before the export.
    assert m.call_count == 1


def test_export_solution_refuses_a_non_crm2011_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    session_mod.save_profile(
        ConnectionProfile(name="v9", url=_URL, domain="CONTOSO", username="alice")
    )
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "v9", "legacy", "export-solution", "x", "-o", str(tmp_path / "x")],
    )
    assert result.exit_code != 0
    assert "not a CRM 2011 profile" in result.output
