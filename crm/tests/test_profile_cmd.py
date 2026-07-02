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

    def test_add_client_secret_alias_stores_secret(self, crm_home):
        # --client-secret reads naturally when scripting an OAuth profile; it is
        # an alias for --password and must store the secret identically.
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://org.crm.dynamics.com",
                "--tenant-id", "t1", "--client-id", "c1",
                "--client-secret", "sekret", "--name", "cloud", "--yes",
            ])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile_secret("cloud") == "sekret"

    def test_add_password_and_client_secret_mutually_exclusive(self, crm_home):
        # Both forms of the same secret is a usage error (exit 2), not a silent
        # last-wins — house rule for mutually-exclusive flags.
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "add",
            "--url", "https://org.crm.dynamics.com",
            "--tenant-id", "t1", "--client-id", "c1",
            "--password", "pw", "--client-secret", "cs", "--name", "cloud", "--yes",
        ])
        assert result.exit_code == 2, result.output
        assert "exclusive" in result.output.lower()


class TestAddWizard:
    """Interactive (TTY) wizard: the auth-scheme step is an inline picker."""

    def test_auth_scheme_inline_picker_preselects_inferred(self, crm_home, monkeypatch):
        # On a TTY with no --auth-scheme, the wizard shows select_one with the
        # URL-inferred scheme preselected; the user's pick wins.
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        captured = {}
        def _fake_select(title, items, default=None):
            captured["default"] = default
            captured["values"] = [v for v, _ in items]
            return "oauth"  # user arrow-keys to oauth
        monkeypatch.setattr("crm.commands.profile.select_one", _fake_select)
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "profile", "add",
                "--url", "https://crm.contoso.local/contoso",
                "--tenant-id", "t1", "--client-id", "c1",
                "--password", "pw", "--name", "wiz", "--yes",
            ], input="\n")  # blank publisher-prefix (wizard now prompts for it)
        assert result.exit_code == 0, result.output
        assert captured["default"] == "ntlm"  # on-prem host -> inferred ntlm
        assert captured["values"] == ["ntlm", "kerberos", "negotiate", "oauth"]
        assert session_mod.load_profile("wiz").auth_scheme == "oauth"

    def test_auth_scheme_picker_cancel_aborts(self, crm_home, monkeypatch):
        # Esc/Ctrl-C at the picker (select_one -> None) aborts cleanly; no
        # profile is written.
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        monkeypatch.setattr("crm.commands.profile.select_one", lambda *a, **k: None)
        runner = CliRunner()
        result = runner.invoke(cli, [
            "profile", "add", "--url", "https://crm.contoso.local/c",
            "--username", "u", "--domain", "D", "--password", "pw",
            "--name", "wiz2", "--yes"])
        assert result.exit_code == 1, result.output  # operational failure (ADR 0001)
        assert "aborted by user" in result.output.lower()
        assert "wiz2" not in session_mod.list_profiles()

    def test_explicit_auth_scheme_skips_picker(self, crm_home, monkeypatch):
        # --auth-scheme given -> no picker, even on a TTY.
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        def _boom(*a, **k):
            raise AssertionError("picker must not run when --auth-scheme is given")
        monkeypatch.setattr("crm.commands.profile.select_one", _boom)
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "profile", "add", "--auth-scheme", "ntlm",
                "--url", "https://crm.contoso.local/c",
                "--username", "u", "--domain", "D", "--password", "pw",
                "--name", "wiz3", "--yes"], input="\n")  # blank publisher-prefix
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("wiz3").auth_scheme == "ntlm"


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
        prof_dir = crm_home / ".crm" / "profiles"
        prof_dir.mkdir(parents=True, exist_ok=True)
        # Two failure shapes: non-JSON, and JSON-valid-but-missing-required-keys
        # (the latter makes from_dict raise KeyError, not JSONDecodeError).
        (prof_dir / "broken.json").write_text("{ this is not valid json", encoding="utf-8")
        (prof_dir / "incomplete.json").write_text('{"name": "incomplete"}', encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "list"])
        assert result.exit_code == 0, result.output
        names = {row["name"] for row in json.loads(result.output)["data"]}
        assert {"good", "broken", "incomplete"} <= names

    def test_use_by_name_survives_a_corrupt_sibling(self, crm_home):
        # A malformed profile must not crash `profile use <good-name>` — the
        # picker-label helper for the sibling falls back to the bare name.
        self._seed("good")
        prof_dir = crm_home / ".crm" / "profiles"
        prof_dir.mkdir(parents=True, exist_ok=True)
        (prof_dir / "incomplete.json").write_text('{"name": "incomplete"}', encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "use", "good"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_session("default")["active_profile"] == "good"

    def test_edit_changes_url(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "edit", "a",
            "--url", "https://new.contoso.local/o2"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("a").url == "https://new.contoso.local/o2"

    def test_edit_blank_username_on_ntlm_fails_fast(self, crm_home):
        # Clearing the username on an on-prem profile must fail at edit time
        # (UsageError, exit 2), not later when a backend is built.
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "edit", "a", "--username", ""])
        assert result.exit_code == 2, result.output
        assert session_mod.load_profile("a").username == "u"  # unchanged

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

    def test_set_password_client_secret_alias(self, crm_home):
        self._seed()
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "set-password", "--profile", "a",
            "--client-secret", "cs"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile_secret("a") == "cs"

    def test_set_password_missing_secret_oauth_hints_client_secret(self, crm_home):
        # On an OAuth profile the missing-secret error must point at the alias,
        # not only --password, so the oauth flag is discoverable when scripting.
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name="cloud", url="https://org.crm.dynamics.com", domain="",
            username="", auth_scheme="oauth", tenant_id="t", client_id="c"))
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "set-password", "--profile", "cloud"])
        assert result.exit_code == 1, result.output
        assert "--client-secret" in json.loads(result.output)["error"]

    def test_set_password_both_secret_flags_mutually_exclusive(self, crm_home):
        self._seed()
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "set-password", "--profile", "a",
            "--password", "pw", "--client-secret", "cs"])
        assert result.exit_code == 2, result.output
        assert "exclusive" in result.output.lower()

    def test_delete_password_removes_secret(self, crm_home):
        self._seed()
        session_mod.save_profile_secret_plaintext("a", "pw")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "delete-password", "--profile", "a"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile_secret("a") is None


class TestPublisherPrefix:
    """The publisher prefix is validated on both the flag and wizard paths."""

    def _seed(self, name="a"):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u"))

    def test_add_invalid_prefix_flag_errors_exit_2(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "add",
            "--url", "https://crm.contoso.local/o",
            "--username", "u", "--domain", "D", "--password", "pw",
            "--name", "p", "--publisher-prefix", "bad!", "--yes"])
        assert result.exit_code == 2, result.output
        assert "prefix" in result.output.lower()
        assert "p" not in session_mod.list_profiles()

    def test_add_valid_prefix_flag_is_stored(self, crm_home):
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://crm.contoso.local/o",
                "--username", "u", "--domain", "D", "--password", "pw",
                "--name", "p", "--publisher-prefix", "new", "--yes"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("p").publisher_prefix == "new"

    def test_edit_invalid_prefix_flag_errors_exit_2(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "edit", "a", "--publisher-prefix", "bad!"])
        assert result.exit_code == 2, result.output
        assert session_mod.load_profile("a").publisher_prefix is None  # unchanged

    def test_wizard_reprompts_on_invalid_then_stores(self, crm_home, monkeypatch):
        # On a TTY with no --publisher-prefix, the wizard prompts; an invalid
        # entry re-prompts, a valid one is stored.
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "profile", "add", "--auth-scheme", "ntlm",
                "--url", "https://crm.contoso.local/o",
                "--username", "u", "--domain", "D", "--password", "pw",
                "--name", "wiz", "--yes"], input="bad!\ngood\n")
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("wiz").publisher_prefix == "good"

    def test_wizard_blank_prefix_skips(self, crm_home, monkeypatch):
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "profile", "add", "--auth-scheme", "ntlm",
                "--url", "https://crm.contoso.local/o",
                "--username", "u", "--domain", "D", "--password", "pw",
                "--name", "wiz", "--yes"], input="\n")
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("wiz").publisher_prefix is None


class TestRename:
    def _seed(self, name, secret=None):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u", publisher_prefix="pfx"))
        if secret is not None:
            session_mod.save_profile_secret_plaintext(name, secret)

    def test_rename_moves_file_name_and_inline_secret(self, crm_home):
        self._seed("old", secret="pw")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "old", "new"])
        assert result.exit_code == 0, result.output
        assert "old" not in session_mod.list_profiles()
        assert "new" in session_mod.list_profiles()
        p = session_mod.load_profile("new")
        assert p.name == "new"
        assert p.publisher_prefix == "pfx"  # other fields ride along
        assert session_mod.load_profile_secret("new") == "pw"

    def test_rename_refuses_to_clobber_existing_new(self, crm_home):
        self._seed("old", secret="pw")
        self._seed("keep", secret="other")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "old", "keep"])
        assert result.exit_code == 1, result.output
        assert "exists" in result.output.lower()
        # both intact
        assert set(session_mod.list_profiles()) >= {"old", "keep"}
        assert session_mod.load_profile_secret("keep") == "other"

    def test_rename_rejects_same_name(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "a", "a"])
        assert result.exit_code == 2, result.output
        assert "differ" in result.output.lower()

    def test_rename_unknown_old_errors(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "ghost", "new"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_rename_rejects_bad_new_name(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "a", "b/c"])
        assert result.exit_code == 1, result.output
        assert "a" in session_mod.list_profiles()  # unchanged

    def test_rename_repoints_active_session(self, crm_home):
        self._seed("old")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "old"])
        result = runner.invoke(cli, ["--json", "profile", "rename", "old", "new"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_session("default")["active_profile"] == "new"

    def test_rename_keyring_failure_is_best_effort_with_hint(self, crm_home, monkeypatch):
        # A keyring-stored secret that fails to move must warn (with a set-password
        # recovery hint) rather than roll back the rename.
        from crm.core import keyring_store as ks
        from crm.utils.d365_backend import D365Error
        self._seed("old")
        monkeypatch.setattr(ks, "get_secret", lambda n: "krsec")
        def _boom(name, secret):
            raise D365Error("keyring locked")
        monkeypatch.setattr(ks, "set_secret", _boom)
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "old", "new"])
        assert result.exit_code == 0, result.output
        assert "new" in session_mod.list_profiles()  # rename still happened
        warnings = (json.loads(result.output).get("meta") or {}).get("warnings") or []
        assert any("set-password new" in w for w in warnings), warnings

    def test_rename_moves_cache_dir(self, crm_home):
        self._seed("old")
        cache_old = crm_home / ".crm" / "cache" / "old"
        cache_old.mkdir(parents=True, exist_ok=True)
        (cache_old / "entitydefs.json").write_text("{}", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "old", "new"])
        assert result.exit_code == 0, result.output
        assert (crm_home / ".crm" / "cache" / "new" / "entitydefs.json").is_file()
        assert not cache_old.exists()


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
