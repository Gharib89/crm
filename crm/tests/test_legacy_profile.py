"""The crm2011 platform marker: profile format, the v9 refusal, profile add and edit."""

# pyright: basic
from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error

_FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
_URL = "https://crm.contoso.local/contoso"
_ENDPOINT = _URL + "/XRMServices/2011/Organization.svc/web"


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    return tmp_path


def _legacy(**kw) -> ConnectionProfile:
    return ConnectionProfile(
        name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011", **kw
    )


def _save_legacy() -> None:
    session_mod.save_profile(_legacy())
    session_mod.save_profile_secret_plaintext("legacy", "pw")


class TestProfileFormat:
    def test_platform_round_trips(self):
        assert ConnectionProfile.from_dict(_legacy().to_dict()).platform == "crm2011"

    def test_profile_json_without_platform_means_v9(self):
        d = _legacy().to_dict()
        del d["platform"]
        assert ConnectionProfile.from_dict(d).platform == "v9"

    def test_crm2011_is_always_read_only(self):
        assert _legacy(read_only=False).read_only is True
        d = _legacy().to_dict()
        d["read_only"] = False  # a hand-edited file cannot clear it either
        assert ConnectionProfile.from_dict(d).read_only is True

    def test_unknown_platform_rejected(self):
        with pytest.raises(D365Error, match="platform"):
            ConnectionProfile(name="x", url=_URL, domain="", username="a", platform="crm4")


class TestV9CommandsRefuseCrm2011:
    def test_backend_refuses_a_crm2011_profile(self):
        with pytest.raises(D365Error, match="crm legacy"):
            D365Backend(_legacy(), "pw")

    @pytest.mark.parametrize(
        "argv",
        [
            ["connection", "whoami"],
            ["entity", "get", "account", "11111111-1111-1111-1111-111111111111"],
        ],
    )
    def test_v9_command_exits_nonzero_with_the_legacy_hint(self, crm_home, argv):
        _save_legacy()
        with requests_mock.Mocker() as m:
            result = CliRunner().invoke(cli, ["--json", "--profile", "legacy", *argv])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "crm legacy" in payload["error"]
        assert m.call_count == 0


def _add(*extra: str):
    return CliRunner().invoke(
        cli,
        [
            "--json",
            "profile",
            "add",
            "--url",
            _URL,
            "--username",
            "alice",
            "--domain",
            "CONTOSO",
            "--password",
            "pw",
            "--name",
            "legacy",
            "--yes",
            *extra,
        ],
    )


class TestProfileAddDetectsCrm2011:
    def test_no_web_api_and_a_5x_build_saves_a_read_only_crm2011_profile(self, crm_home):
        with requests_mock.Mocker() as m:
            m.get(
                requests_mock.ANY,
                status_code=404,
                text=(_FIXTURES / "iis_404.html").read_text(),
                headers={"Content-Type": "text/html"},
            )
            m.post(
                _ENDPOINT,
                text=(_FIXTURES / "retrieve_version.xml").read_text(),
                headers={"Content-Type": "text/xml; charset=utf-8"},
            )
            result = _add()
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["platform"] == "crm2011"
        assert data["read_only"] is True
        saved = session_mod.load_profile("legacy")
        assert saved.platform == "crm2011"
        assert saved.read_only is True

    def test_a_404_with_no_crm2011_answer_keeps_the_original_failure(self, crm_home):
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, status_code=404, json={"error": {"message": "nope"}})
            m.post(_ENDPOINT, status_code=404, text="", headers={"Content-Type": "text/html"})
            result = _add()
        assert result.exit_code == 1, result.output
        assert "legacy" not in session_mod.list_profiles()

    def test_a_v9_org_is_unchanged(self, crm_home):
        whoami = {
            "UserId": "11111111-1111-1111-1111-111111111111",
            "BusinessUnitId": "22222222-2222-2222-2222-222222222222",
            "OrganizationId": "33333333-3333-3333-3333-333333333333",
        }
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=whoami)
            result = _add()
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["platform"] == "v9"
        assert not any(r.method == "POST" for r in m.request_history)


class TestProfileEditCannotClearCrm2011ReadOnly:
    def test_no_read_only_refused_even_on_a_tty(self, crm_home, monkeypatch):
        _save_legacy()
        import crm.commands.profile as profile_cmd

        monkeypatch.setattr(profile_cmd, "_stdin_is_tty", lambda: True)
        result = CliRunner().invoke(
            cli, ["profile", "edit", "legacy", "--no-read-only"], input="y\n"
        )
        assert result.exit_code == 1, result.output
        assert "crm2011" in result.output
        assert session_mod.load_profile("legacy").read_only is True
