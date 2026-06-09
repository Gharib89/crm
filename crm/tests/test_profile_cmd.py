"""Tests for the `crm profile` command group."""
# pyright: basic
from __future__ import annotations

import json
import re

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod

_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001",
           "BusinessUnitId": "00000000-0000-0000-0000-0000000000bb",
           "OrganizationId": "00000000-0000-0000-0000-0000000000cc"}


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks
    monkeypatch.setattr(ks, "is_available", lambda: False)
    monkeypatch.setattr(ks, "delete_secret", lambda n: False)
    # Stub OAuth auth so an oauth-profile backend never reaches msal/AAD at
    # construction (msal uses its own HTTP client, bypassing requests_mock).
    from crm.utils.d365_backend import D365Backend
    monkeypatch.setattr(D365Backend, "_make_oauth_auth",
                        lambda self, secret: (lambda r: r))
    return tmp_path


class TestAddScriptable:
    def test_add_ntlm_saves_profile_and_secret_and_activates(self, crm_home):
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://crm.contoso.local/contoso",
                "--username", "alice", "--domain", "CONTOSO",
                "--password", "pw", "--name", "contoso", "--yes",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["profile"] == "contoso"
        assert payload["data"]["credential_storage"] == "plaintext"
        assert "contoso" in session_mod.list_profiles()
        state = session_mod.load_session("default")
        assert state["active_profile"] == "contoso"
        assert session_mod.load_profile_secret("contoso") == "pw"

    def test_add_infers_oauth_from_dynamics_url(self, crm_home):
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://org.crm.dynamics.com",
                "--tenant-id", "t1", "--client-id", "c1",
                "--password", "secret", "--name", "cloud", "--yes",
            ])
        assert result.exit_code == 0, result.output
        p = session_mod.load_profile("cloud")
        assert p.auth_scheme == "oauth"
        assert p.tenant_id == "t1" and p.client_id == "c1"

    def test_add_negotiates_api_version_down_on_501(self, crm_home):
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            # v9.2 probe 501 (on-prem cap); v9.1 retry succeeds.
            m.get(re.compile(r"/api/data/v9\.2/"), status_code=501,
                  json={"error": {"message": "Not Implemented"}})
            m.get(re.compile(r"/api/data/v9\.1/"), json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://crm.contoso.local/contoso",
                "--username", "alice", "--domain", "CONTOSO",
                "--password", "pw", "--name", "negc", "--yes",
            ])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("negc").api_version == "v9.1"

    def test_add_missing_url_in_json_mode_errors_cleanly(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "add", "--name", "x"])
        assert result.exit_code == 2, result.output


class TestUse:
    def _seed(self, name):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u"))

    def test_use_by_name_switches_active(self, crm_home):
        self._seed("a"); self._seed("b")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "use", "b"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_session("default")["active_profile"] == "b"

    def test_use_unknown_name_errors(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "use", "ghost"])
        assert result.exit_code == 1, result.output

    def test_use_none_clears_active(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "a"])
        result = runner.invoke(cli, ["--json", "profile", "use", "--none"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_session("default")["active_profile"] is None

    def test_use_no_arg_no_tty_errors(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "use"])
        assert result.exit_code in (1, 2), result.output


class TestListEditRm:
    def _seed(self, name):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u"))

    def test_list_marks_active(self, crm_home):
        self._seed("a"); self._seed("b")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "a"])
        result = runner.invoke(cli, ["--json", "profile", "list"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        active = {row["name"]: row["active"] for row in data}
        assert active["a"] is True and active["b"] is False

    def test_list_survives_corrupt_profile(self, crm_home):
        self._seed("good")
        # Corrupt a second profile file directly on disk.
        prof_dir = crm_home / ".crm" / "profiles"
        prof_dir.mkdir(parents=True, exist_ok=True)
        (prof_dir / "broken.json").write_text("{ this is not valid json", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "list"])
        assert result.exit_code == 0, result.output
        names = {row["name"] for row in json.loads(result.output)["data"]}
        assert "good" in names and "broken" in names

    def test_edit_changes_url(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "edit", "a",
            "--url", "https://new.contoso.local/o2"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("a").url == "https://new.contoso.local/o2"

    def test_rm_deletes_profile_and_secret(self, crm_home):
        self._seed("a")
        session_mod.save_profile_secret_plaintext("a", "pw")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rm", "a", "--yes"])
        assert result.exit_code == 0, result.output
        assert "a" not in session_mod.list_profiles()

    def test_rm_active_clears_session_pointer(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "a"])
        runner.invoke(cli, ["--json", "profile", "rm", "a", "--yes"])
        assert session_mod.load_session("default")["active_profile"] is None


class TestSetDeletePassword:
    def _seed(self, name="a"):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u"))

    def test_set_password_stores_plaintext_when_no_keyring(self, crm_home):
        self._seed()
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "set-password", "--profile", "a",
            "--password", "pw"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile_secret("a") == "pw"

    def test_delete_password_removes_secret(self, crm_home):
        self._seed()
        session_mod.save_profile_secret_plaintext("a", "pw")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "delete-password", "--profile", "a"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile_secret("a") is None


class TestAutoLaunch:
    def test_no_profile_json_mode_errors_no_hang(self, crm_home):
        # whoami with no profile, under --json (no TTY) -> clean error, exit 1.
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "connection", "whoami"])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "crm profile add" in payload["error"]


class TestAuthHints:
    def test_401_whoami_prints_set_password_hint(self, crm_home):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name="cloud", url="https://org.crm.dynamics.com",
            domain="", username="", auth_scheme="oauth",
            tenant_id="t", client_id="c"))
        session_mod.save_profile_secret_plaintext("cloud", "badsecret")
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, status_code=401, json={"error": {"message": "unauthorized"}})
            result = runner.invoke(cli, ["--json", "--profile", "cloud", "connection", "whoami"])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert "set-password" in (payload.get("meta", {}).get("hint", "")
                                  or payload.get("error", ""))
