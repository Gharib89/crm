"""Delegated sign-in (`oauth-user`, #1145): silent token reuse, `profile login`, profile verbs.

msal is faked throughout; nothing reaches Entra. The live sign-in proof is #1146.
"""

# pyright: basic
from __future__ import annotations

import json
import sys
import types
import webbrowser
from typing import Any

import pytest
import requests
from click.testing import CliRunner

from crm.cli import cli
from crm.core import keyring_store, token_store, user_signin
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error, is_cloud

pytestmark = pytest.mark.usefixtures("isolated_home")

TENANT = "11111111-1111-1111-1111-111111111111"
HOME_ID = "uid.utid"
ACCOUNT = {"home_account_id": HOME_ID, "username": "dev@example.com"}


class FakeMsal:
    """A stand-in `msal` module; `state` records calls and scripts the answers."""

    def __init__(self) -> None:
        self.silent: Any = {"access_token": "TOK"}
        self.interactive: Any = None
        self.device_flow: Any = {"user_code": "ABC", "message": "go to the page"}
        self.device_result: Any = None
        self.accounts: list[dict[str, str]] = [dict(ACCOUNT)]
        self.removed: list[dict[str, str]] = []
        self.calls: list[str] = []
        fake = self

        class SerializableTokenCache:
            def __init__(self):
                self.has_state_changed = False
                self._blob = ""

            def deserialize(self, blob):
                self._blob = blob

            def serialize(self):
                return self._blob or '{"cache": 1}'

        class PublicClientApplication:
            def __init__(self, client_id, *, authority=None, token_cache=None):
                fake.calls.append(f"app:{client_id}:{authority}")
                self.cache = token_cache

            def get_accounts(self, username=None):
                return [
                    a
                    for a in fake.accounts
                    if username is None or a["username"].lower() == username.lower()
                ]

            def acquire_token_silent_with_error(self, scopes, account=None):
                fake.calls.append("silent")
                if isinstance(fake.silent, Exception):
                    raise fake.silent
                self.cache.has_state_changed = True  # type: ignore[union-attr]
                return fake.silent

            def acquire_token_interactive(self, scopes, login_hint=None):
                fake.calls.append("interactive")
                self.cache.has_state_changed = True  # type: ignore[union-attr]
                return fake.interactive

            def initiate_device_flow(self, scopes=None):
                fake.calls.append("device")
                return fake.device_flow

            def acquire_token_by_device_flow(self, flow):
                self.cache.has_state_changed = True  # type: ignore[union-attr]
                return fake.device_result

            def remove_account(self, account):
                fake.removed.append(account)

        mod: Any = types.ModuleType("msal")
        mod.SerializableTokenCache = SerializableTokenCache
        mod.PublicClientApplication = PublicClientApplication
        self.module = mod


@pytest.fixture
def msal(monkeypatch):
    fake = FakeMsal()
    monkeypatch.setitem(sys.modules, "msal", fake.module)
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)
    monkeypatch.setattr(keyring_store, "delete_secret", lambda n: False)
    return fake


def _profile(**kw) -> ConnectionProfile:
    base: dict[str, Any] = {
        "name": "dev",
        "url": "https://contoso.crm.dynamics.com",
        "domain": "",
        "username": "",
        "auth_scheme": "oauth-user",
        "tenant_id": TENANT,
        "client_id": "cid",
        "home_account_id": HOME_ID,
    }
    base.update(kw)
    return ConnectionProfile(**base)


def _ok(tid=TENANT, name="dev@example.com"):
    return {"access_token": "T", "id_token_claims": {"tid": tid, "preferred_username": name}}


class TestSilentAcquire:
    def test_token_is_injected_and_cache_saved(self, msal):
        p = _profile()
        req = requests.Request("GET", p.url).prepare()
        auth: Any = D365Backend(p, "").session.auth
        auth(req)
        assert req.headers["Authorization"] == "Bearer TOK"
        assert token_store.load("dev")
        assert "interactive" not in msal.calls and "device" not in msal.calls

    def test_scope_is_the_orgs_user_impersonation(self):
        assert (
            user_signin.scope_for(_profile())
            == "https://contoso.crm.dynamics.com/user_impersonation"
        )

    @pytest.mark.parametrize(
        "setup",
        [
            pytest.param(lambda m, p: setattr(p, "home_account_id", None), id="no-bound-account"),
            pytest.param(lambda m, p: setattr(m, "accounts", []), id="account-not-in-cache"),
            pytest.param(lambda m, p: setattr(m, "silent", None), id="none-result"),
            pytest.param(
                lambda m, p: setattr(m, "silent", {"error": "invalid_grant"}), id="revoked"
            ),
            pytest.param(
                lambda m, p: setattr(m, "silent", {"error": "interaction_required"}), id="expired"
            ),
            pytest.param(lambda m, p: setattr(m, "silent", OSError("boom")), id="exception"),
        ],
    )
    def test_failures_become_reauth_required(self, msal, setup):
        p = _profile()
        setup(msal, p)
        with pytest.raises(D365Error) as ei:
            user_signin.acquire_silent(p)
        assert ei.value.code == "reauth_required"
        assert ei.value.status == 401
        assert "crm profile login dev" in str(ei.value)
        assert "interactive" not in msal.calls and "device" not in msal.calls

    def test_error_envelope_hint_names_login(self, msal, monkeypatch):
        msal.silent = None
        session_mod.save_profile(_profile())
        monkeypatch.setattr(
            "crm.core.connection.session_mod", session_mod
        )  # same module; keeps the import explicit
        r = CliRunner().invoke(cli, ["--json", "--profile", "dev", "connection", "whoami"])
        env = json.loads(r.stdout)
        assert env["ok"] is False
        assert env["meta"]["code"] == "reauth_required"
        assert env["meta"]["status"] == 401
        assert env["meta"]["hint"] == "Run crm profile login dev"
        assert "TOK" not in r.stdout


class TestLogin:
    def test_browser_sign_in_binds_the_account(self, msal, monkeypatch):
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = _ok()
        p = _profile(home_account_id=None)
        out = user_signin.login(p, device_code=False)
        assert out["account"] == HOME_ID and out["previous"] is None
        assert out["username"] == "dev@example.com"
        assert token_store.load("dev")

    def test_no_browser_and_no_flag_fails_at_once_naming_the_flag(self, msal, monkeypatch):
        def no_browser(*a):
            raise webbrowser.Error("no runnable browser")

        monkeypatch.setattr(webbrowser, "get", no_browser)
        with pytest.raises(D365Error, match="--device-code"):
            user_signin.login(_profile(), device_code=False)
        assert msal.calls == []  # never reached MSAL, which would wait out its timeout

    def test_wrong_tenant_is_refused_and_nothing_saved(self, msal, monkeypatch):
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = _ok(tid="22222222-2222-2222-2222-222222222222")
        with pytest.raises(D365Error) as ei:
            user_signin.login(_profile(), device_code=False)
        assert ei.value.code == "wrong_tenant"
        assert token_store.load("dev") == ""

    def test_rebinding_a_different_account_removes_the_old_one(self, msal, monkeypatch):
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = _ok(name="other@example.com")
        msal.accounts = [
            dict(ACCOUNT),
            {"home_account_id": "new.id", "username": "other@example.com"},
        ]
        out = user_signin.login(_profile(), device_code=False)
        assert out["account"] == "new.id" and out["previous"] == HOME_ID
        assert msal.removed == [ACCOUNT]

    def test_device_code_flow(self, msal, capsys):
        msal.device_result = _ok()
        out = user_signin.login(_profile(home_account_id=None), device_code=True)
        assert out["account"] == HOME_ID
        assert "go to the page" in capsys.readouterr().err

    @pytest.mark.parametrize("code", ["AADSTS65001", "AADSTS90094"])
    def test_consent_errors_name_the_fix(self, msal, monkeypatch, code):
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = {"error": "invalid_grant", "error_description": f"{code}: no consent"}
        with pytest.raises(D365Error, match="admin consent"):
            user_signin.login(_profile(), device_code=False)

    def test_conditional_access_block_of_device_code_names_the_fix(self, msal):
        msal.device_flow = {
            "error": "access_denied",
            "error_description": "AADSTS53003: blocked by Conditional Access",
        }
        with pytest.raises(D365Error, match="without --device-code"):
            user_signin.login(_profile(), device_code=True)


class TestProfileLoginCommand:
    def _tty(self, monkeypatch, value=True):
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: value)

    def test_json_refuses(self, msal, monkeypatch):
        self._tty(monkeypatch)
        session_mod.save_profile(_profile())
        r = CliRunner().invoke(cli, ["--json", "profile", "login", "dev"])
        env = json.loads(r.stdout)
        assert r.exit_code == 1 and env["ok"] is False
        assert env["meta"]["code"] == "login_requires_tty"
        assert msal.calls == []

    def test_no_tty_refuses(self, msal, monkeypatch):
        self._tty(monkeypatch, False)
        session_mod.save_profile(_profile())
        r = CliRunner().invoke(cli, ["profile", "login", "dev"])
        assert r.exit_code == 1 and "interactive" in r.output
        assert msal.calls == []

    def test_login_saves_the_binding(self, msal, monkeypatch):
        self._tty(monkeypatch)
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = _ok()
        session_mod.save_profile(_profile(home_account_id=None))
        r = CliRunner().invoke(cli, ["profile", "login", "dev"])
        assert r.exit_code == 0, r.output
        saved = session_mod.load_profile("dev")
        assert saved.home_account_id == HOME_ID and saved.username == "dev@example.com"

    def test_refuses_a_non_delegated_profile(self, msal, monkeypatch):
        self._tty(monkeypatch)
        session_mod.save_profile(_profile(auth_scheme="oauth", home_account_id=None))
        r = CliRunner().invoke(cli, ["profile", "login", "dev"])
        assert r.exit_code == 1 and "oauth-user" in r.output


class TestProfileVerbs:
    def test_add_with_flags_saves_without_a_secret(self, msal):
        r = CliRunner().invoke(
            cli,
            [
                "--json", "profile", "add",
                "--url", "https://contoso.crm.dynamics.com",
                "--name", "dev",
                "--auth-scheme", "oauth-user",
                "--tenant-id", TENANT,
                "--client-id", "cid",
            ],
        )  # fmt: skip
        env = json.loads(r.stdout)
        assert r.exit_code == 0 and env["data"]["credential_storage"] == "none"
        assert "profile login dev" in env["meta"]["warnings"][0]
        assert session_mod.load_profile("dev").auth_scheme == "oauth-user"
        assert msal.calls == []

    def test_add_rejects_a_secret(self, msal):
        r = CliRunner().invoke(
            cli,
            ["profile", "add", "--url", "https://c.crm.dynamics.com", "--name", "dev",
             "--auth-scheme", "oauth-user", "--tenant-id", TENANT, "--client-id", "c",
             "--password", "x"],
        )  # fmt: skip
        assert r.exit_code == 2 and "no secret" in r.output

    def test_scripted_cloud_add_still_infers_oauth(self):
        from crm.commands.profile import infer_auth_scheme

        assert infer_auth_scheme("https://contoso.crm.dynamics.com") == "oauth"

    @pytest.mark.parametrize("verb", ["set-password", "delete-password"])
    def test_secret_verbs_point_to_login(self, msal, verb):
        session_mod.save_profile(_profile())
        r = CliRunner().invoke(
            cli,
            ["profile", verb, "--profile", "dev", "--password", "x"][
                : 4 if verb == "delete-password" else 6
            ],
        )
        assert r.exit_code == 2
        assert "profile login dev" in r.output

    def test_rm_removes_the_cached_account(self, msal):
        session_mod.save_profile(_profile())
        token_store.save("dev", '{"c": 1}')
        r = CliRunner().invoke(cli, ["profile", "rm", "dev", "--yes"])
        assert r.exit_code == 0, r.output
        assert token_store.load("dev") == "" and not token_store.cache_path("dev").exists()

    def test_rename_drops_the_cache_and_says_to_log_in_again(self, msal):
        session_mod.save_profile(_profile())
        token_store.save("dev", '{"c": 1}')
        r = CliRunner().invoke(cli, ["--json", "profile", "rename", "dev", "dev2"])
        env = json.loads(r.stdout)
        assert r.exit_code == 0 and "profile login dev2" in env["meta"]["warnings"][0]
        assert not token_store.cache_path("dev").exists()


class TestOtherSchemesUnchanged:
    def test_is_cloud_covers_both_cloud_schemes_only(self):
        for scheme, want in [
            ("oauth", True),
            ("oauth-user", True),
            ("ntlm", False),
            ("adfs", False),
        ]:
            assert is_cloud(_profile(auth_scheme=scheme, username="u")) is want

    @pytest.mark.parametrize("scheme", ["ntlm", "adfs", "oauth"])
    def test_other_schemes_still_demand_a_secret(self, scheme):
        from crm.core import connection as conn

        session_mod.save_profile(_profile(auth_scheme=scheme, username="u", home_account_id=None))
        with pytest.raises(D365Error, match="No .* stored"):
            conn.resolve_credentials("dev")
        assert conn.resolve_credentials("dev", "s3").password == "s3"

    def test_oauth_user_resolves_with_no_secret(self):
        from crm.core import connection as conn

        session_mod.save_profile(_profile())
        assert conn.resolve_credentials("dev").password == ""

    def test_auth_adapters_per_scheme(self, monkeypatch):
        from requests.auth import AuthBase

        from crm.utils.adfs import AdfsCookieAuth

        monkeypatch.setitem(sys.modules, "msal", FakeMsal().module)
        assert isinstance(D365Backend(_profile(auth_scheme="oauth"), "s").session.auth, AuthBase)
        adfs = D365Backend(_profile(auth_scheme="adfs", username="u"), "pw")
        assert isinstance(adfs.session.auth, AdfsCookieAuth)
        ntlm = D365Backend(_profile(auth_scheme="ntlm", username="u"), "pw")
        assert type(ntlm.session.auth).__name__ == "HttpNtlmAuth"
        user = D365Backend(_profile(), "")
        assert type(user.session.auth).__name__ == "UserBearerAuth"
