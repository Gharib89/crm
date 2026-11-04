"""`crm legacy whoami` / `crm legacy version` over a mocked CRM 2011 /web endpoint."""

# pyright: basic
from __future__ import annotations

import json
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


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    return tmp_path


def _save(name: str, platform: str) -> None:
    session_mod.save_profile(
        ConnectionProfile(
            name=name, url=_URL, domain="CONTOSO", username="alice", platform=platform
        )
    )
    session_mod.save_profile_secret_plaintext(name, "pw")


def _run(fixture: str, *argv: str):
    with requests_mock.Mocker() as m:
        m.post(
            _ENDPOINT,
            text=(_FIXTURES / fixture).read_text(),
            headers={"Content-Type": "text/xml; charset=utf-8"},
        )
        result = CliRunner().invoke(cli, ["--json", *argv])
    return result, m


def test_version_returns_the_build(crm_home):
    _save("legacy", "crm2011")
    result, _ = _run("retrieve_version.xml", "legacy", "version", "--profile", "legacy")
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["data"] == {"version": "5.0.9690.3557"}
    assert env["meta"]["profile"] == "legacy"
    assert env["meta"]["url"] == _ENDPOINT


def test_whoami_returns_the_three_ids(crm_home):
    _save("legacy", "crm2011")
    result, _ = _run("whoami.xml", "--profile", "legacy", "legacy", "whoami")
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["data"] == {
        "user_id": "11111111-1111-1111-1111-111111111111",
        "business_unit_id": "22222222-2222-2222-2222-222222222222",
        "organization_id": "33333333-3333-3333-3333-333333333333",
    }
    assert env["meta"] == {"profile": "legacy", "url": _ENDPOINT}


def test_a_fault_exits_1_with_its_code(crm_home):
    _save("legacy", "crm2011")
    with requests_mock.Mocker() as m:
        m.post(
            _ENDPOINT,
            status_code=500,
            text=(_FIXTURES / "fault_not_found.xml").read_text(),
            headers={"Content-Type": "text/xml; charset=utf-8"},
        )
        result = CliRunner().invoke(cli, ["--json", "--profile", "legacy", "legacy", "whoami"])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["meta"]["code"] == "0x80040217"
    assert env["meta"]["category"] == "not_found"


@pytest.mark.parametrize("verb", ["whoami", "version"])
def test_legacy_refuses_a_v9_profile(crm_home, verb):
    _save("v9", "v9")
    result, m = _run("whoami.xml", "--profile", "v9", "legacy", verb)
    assert result.exit_code == 1, result.output
    assert "not a CRM 2011" in json.loads(result.output)["error"]
    assert m.call_count == 0


def test_legacy_refuses_a_v9_profile_before_any_secret_lookup(crm_home):
    session_mod.save_profile(
        ConnectionProfile(name="nosecret", url=_URL, domain="CONTOSO", username="alice")
    )
    result = CliRunner().invoke(cli, ["--json", "--profile", "nosecret", "legacy", "version"])
    assert result.exit_code == 1, result.output
    assert "not a CRM 2011" in json.loads(result.output)["error"]


def test_legacy_refuses_a_non_ntlm_auth_scheme_override(crm_home):
    _save("legacy", "crm2011")
    result, m = _run(
        "retrieve_version.xml",
        "--auth-scheme",
        "kerberos",
        "--profile",
        "legacy",
        "legacy",
        "version",
    )
    assert result.exit_code == 1, result.output
    assert "--auth-scheme kerberos" in json.loads(result.output)["error"]
    assert m.call_count == 0


def test_a_401_hint_names_the_active_profile(crm_home):
    _save("legacy", "crm2011")
    session_mod.save_session({"active_profile": "legacy"})
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, status_code=401, text="", headers={"Content-Type": "text/plain"})
        result = CliRunner().invoke(cli, ["--json", "legacy", "whoami"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["meta"]["hint"].endswith("--profile legacy")


@pytest.mark.parametrize(
    ("verb", "fixture"), [("whoami", "whoami.xml"), ("version", "retrieve_version.xml")]
)
def test_human_mode_prints_no_connection_meta(crm_home, verb, fixture):
    _save("legacy", "crm2011")
    with requests_mock.Mocker() as m:
        m.post(
            _ENDPOINT, text=(_FIXTURES / fixture).read_text(), headers={"Content-Type": "text/xml"}
        )
        result = CliRunner().invoke(cli, ["--profile", "legacy", "legacy", verb])
    assert result.exit_code == 0, result.output
    assert _ENDPOINT not in result.output
