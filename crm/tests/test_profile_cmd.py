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
            # "\n" satisfies the optional publisher-prefix prompt (empty = skip).
            result = runner.invoke(cli, [
                "profile", "add",
                "--url", "https://crm.contoso.local/contoso",
                "--tenant-id", "t1", "--client-id", "c1",
                "--password", "pw", "--name", "wiz", "--yes",
            ], input="\n")
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
            # "\n" satisfies the optional publisher-prefix prompt (empty = skip).
            result = runner.invoke(cli, [
                "profile", "add", "--auth-scheme", "ntlm",
                "--url", "https://crm.contoso.local/c",
                "--username", "u", "--domain", "D", "--password", "pw",
                "--name", "wiz3", "--yes"], input="\n")
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


# ── Helpers shared by rename + prefix tests ──────────────────────────────


def _seed_profile(name, *, secret=None, publisher_prefix=None):
    """Create a minimal NTLM profile in the current CRM_HOME."""
    from crm.utils.d365_backend import ConnectionProfile
    p = ConnectionProfile(
        name=name, url=f"https://{name}.contoso.local/o",
        domain="C", username="u",
        publisher_prefix=publisher_prefix,
    )
    session_mod.save_profile(p)
    if secret is not None:
        session_mod.save_profile_secret_plaintext(name, secret)


class TestRename:
    """crm profile rename OLD NEW — file, session-pointer, keyring, cache."""

    def test_rename_moves_file_and_updates_internal_name(self, crm_home):
        _seed_profile("alpha")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "alpha", "bravo"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["old"] == "alpha"
        assert payload["data"]["new"] == "bravo"
        assert "alpha" not in session_mod.list_profiles()
        assert "bravo" in session_mod.list_profiles()
        p = session_mod.load_profile("bravo")
        assert p.name == "bravo"

    def test_rename_carries_inline_secret(self, crm_home):
        _seed_profile("alpha", secret="topsecret")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "alpha", "bravo"])
        assert result.exit_code == 0, result.output
        # old profile must be gone
        assert session_mod.load_profile_secret("alpha") is None
        # new profile must have the secret
        assert session_mod.load_profile_secret("bravo") == "topsecret"

    def test_rename_repoints_active_session_when_old_is_active(self, crm_home):
        _seed_profile("alpha")
        # Activate alpha first.
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "alpha"])
        assert session_mod.load_session("default")["active_profile"] == "alpha"

        result = runner.invoke(cli, ["--json", "profile", "rename", "alpha", "bravo"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["data"]["active_updated"] is True
        assert payload["meta"]["profile"] == "bravo"
        assert session_mod.load_session("default")["active_profile"] == "bravo"

    def test_rename_does_not_touch_session_when_old_is_not_active(self, crm_home):
        _seed_profile("alpha")
        _seed_profile("other")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "other"])

        result = runner.invoke(cli, ["--json", "profile", "rename", "alpha", "bravo"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["data"]["active_updated"] is False
        # meta.profile must reflect the still-active 'other', not the renamed 'bravo'.
        assert payload["meta"]["profile"] == "other"
        # active pointer must still be 'other'
        assert session_mod.load_session("default")["active_profile"] == "other"

    def test_rename_refuses_to_clobber_existing_new_name(self, crm_home):
        _seed_profile("alpha")
        _seed_profile("bravo")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "alpha", "bravo"])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "bravo" in payload["error"]
        # Both profiles must still exist — no partial mutation.
        assert "alpha" in session_mod.list_profiles()
        assert "bravo" in session_mod.list_profiles()

    def test_rename_errors_when_old_missing(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "ghost", "new"])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "ghost" in payload["error"]

    def test_rename_moves_cache_dir(self, crm_home):
        _seed_profile("alpha")
        cache_dir = session_mod._state_root() / "cache" / "alpha"
        cache_dir.mkdir(parents=True)
        (cache_dir / "marker.json").write_text("{}", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rename", "alpha", "bravo"])
        assert result.exit_code == 0, result.output

        new_cache_dir = session_mod._state_root() / "cache" / "bravo"
        assert not cache_dir.exists()
        assert (new_cache_dir / "marker.json").is_file()

    def test_rename_invalid_new_name_errors_before_any_mutation(self, crm_home):
        _seed_profile("alpha")
        runner = CliRunner()
        # A name with a path separator is invalid.
        result = runner.invoke(cli, ["--json", "profile", "rename", "alpha", "bad/name"])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        # alpha must still exist untouched.
        assert "alpha" in session_mod.list_profiles()


class TestRenameSessionHelper:
    """Unit tests for session_mod.rename_profile (the strict-typed helper)."""

    def test_helper_moves_file_and_returns_pointer_flag(self, crm_home):
        _seed_profile("x")
        # Activate x in the default session.
        state = session_mod.load_session("default")
        state["active_profile"] = "x"
        session_mod.save_session(state, "default")

        updated = session_mod.rename_profile("x", "y")
        assert updated is True
        assert "x" not in session_mod.list_profiles()
        assert "y" in session_mod.list_profiles()
        assert session_mod.load_profile("y").name == "y"
        assert session_mod.load_session("default")["active_profile"] == "y"

    def test_helper_raises_file_not_found_for_missing_old(self, crm_home):
        with pytest.raises(FileNotFoundError, match="ghost"):
            session_mod.rename_profile("ghost", "new")

    def test_helper_raises_file_exists_for_existing_new(self, crm_home):
        _seed_profile("a"); _seed_profile("b")
        with pytest.raises(FileExistsError, match="b"):
            session_mod.rename_profile("a", "b")

    def test_helper_drops_legacy_default_solution_key(self, crm_home):
        """A pre-#623 profile carrying `default_solution` loads fine and drops
        the key on next save — renaming counts as a save (session.py:233)."""
        _seed_profile("legacy")
        raw = json.loads(session_mod.profile_path("legacy").read_text(encoding="utf-8"))
        raw["default_solution"] = "MySolution"
        session_mod.profile_path("legacy").write_text(json.dumps(raw), encoding="utf-8")

        session_mod.rename_profile("legacy", "modern")

        new_raw = json.loads(session_mod.profile_path("modern").read_text(encoding="utf-8"))
        assert "default_solution" not in new_raw


class TestPublisherPrefixValidation:
    """publisher_prefix is validated on both the flag path and the wizard."""

    def test_flag_bad_prefix_errors_with_exit2(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "add",
            "--url", "https://crm.contoso.local/org",
            "--username", "alice", "--domain", "CONTOSO",
            "--password", "pw", "--name", "p1", "--yes",
            "--publisher-prefix", "!bad!",
        ])
        assert result.exit_code == 2, result.output

    def test_flag_mscrm_prefix_errors(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "add",
            "--url", "https://crm.contoso.local/org",
            "--username", "alice", "--domain", "CONTOSO",
            "--password", "pw", "--name", "p1", "--yes",
            "--publisher-prefix", "mscrm",
        ])
        assert result.exit_code == 2, result.output

    def test_flag_valid_prefix_is_stored(self, crm_home):
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://crm.contoso.local/org",
                "--username", "alice", "--domain", "CONTOSO",
                "--password", "pw", "--name", "p1", "--yes",
                "--publisher-prefix", "contoso",
            ])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("p1").publisher_prefix == "contoso"

    def test_wizard_bad_prefix_reprompts_then_accepts_good(self, crm_home, monkeypatch):
        """An invalid prefix in the wizard loop re-prompts; a good one is stored."""
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        # Stub the auth-scheme picker so we don't need a full TTY.
        monkeypatch.setattr("crm.commands.profile.select_one", lambda *a, **k: "ntlm")

        prompts = iter(["alice", "CONTOSO", "mywiz", "pw", "!bad!", "good"])
        monkeypatch.setattr("click.prompt", lambda msg, **kw: next(prompts))

        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "profile", "add",
                "--url", "https://crm.contoso.local/org",
                "--yes",
            ])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("mywiz").publisher_prefix == "good"

    def test_wizard_empty_prefix_skips_and_stores_none(self, crm_home, monkeypatch):
        """Empty prefix input in the wizard skips the field (None stored)."""
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        monkeypatch.setattr("crm.commands.profile.select_one", lambda *a, **k: "ntlm")

        # username, domain, name, password, publisher_prefix (empty → skip)
        prompts = iter(["alice", "CONTOSO", "wiz2", "pw", ""])
        monkeypatch.setattr("click.prompt", lambda msg, **kw: next(prompts))

        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "profile", "add",
                "--url", "https://crm.contoso.local/org",
                "--yes",
            ])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("wiz2").publisher_prefix is None
