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


class BrowserInteractionTimeoutError(RuntimeError):
    """MSAL raises this (not an error dict) when `timeout` passes with no redirect."""


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
        self.interactive_kwargs: dict[str, Any] = {}
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

            def acquire_token_interactive(
                self, scopes, login_hint=None, timeout=None, on_before_launching_ui=None
            ):
                fake.calls.append("interactive")
                fake.interactive_kwargs = {"timeout": timeout}
                if on_before_launching_ui:
                    on_before_launching_ui(ui="browser")  # MSAL calls it just before it waits
                fake.calls.append("wait")
                if isinstance(fake.interactive, Exception):
                    raise fake.interactive
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
        self.timeout_error = BrowserInteractionTimeoutError
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

    def test_app_is_built_once_per_backend(self, msal):
        p = _profile()
        auth: Any = D365Backend(p, "").session.auth
        for _ in range(3):
            auth(requests.Request("GET", p.url).prepare())
        assert sum(c.startswith("app:") for c in msal.calls) == 1
        assert msal.calls.count("silent") == 3

    def test_login_failure_hint_is_not_set_password(self, msal, monkeypatch):
        monkeypatch.setattr("crm.commands.profile._stdin_is_tty", lambda: True)
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = _ok(tid="22222222-2222-2222-2222-222222222222")
        session_mod.save_profile(_profile())
        r = CliRunner().invoke(cli, ["profile", "login", "dev"])
        assert r.exit_code == 1 and "set-password" not in r.output
        assert "crm profile login dev to retry" in r.output

    def test_server_401_without_a_code_hints_login_not_set_password(self):
        from crm.commands._helpers.errors import _handle_d365_error

        emitted: dict[str, Any] = {}
        ctx: Any = types.SimpleNamespace(
            _backend=types.SimpleNamespace(profile=_profile()),
            legacy_profile_name=None,
            profile_name="dev",
            json_mode=True,
            emit=lambda ok, **kw: emitted.update(kw),
        )
        _handle_d365_error(ctx, D365Error("user disabled", status=401))
        assert emitted["meta"]["hint"] == "Run crm profile login dev"
        assert "set-password" not in emitted["error"]

    def test_unwritable_cache_does_not_fail_a_silent_token(self, msal, monkeypatch):
        def boom(name, blob):
            raise OSError("disk full")

        monkeypatch.setattr(token_store, "save", boom)
        assert user_signin.acquire_silent(_profile()) == "TOK"

    def test_login_reports_an_unwritable_cache_as_an_error(self, msal, monkeypatch):
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = _ok()

        def boom(name, blob):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(token_store, "save", boom)
        with pytest.raises(D365Error) as ei:
            user_signin.login(_profile(home_account_id=None), device_code=False)
        assert ei.value.code == "cache_write_failed"
        assert "Signed in, but the token cache" in str(ei.value)


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

    def test_device_code_flow(self, msal):
        msal.device_result = _ok()
        shown: list[str] = []
        out = user_signin.login(
            _profile(home_account_id=None), device_code=True, announce=shown.append
        )
        assert out["account"] == HOME_ID
        assert shown == ["go to the page"]  # the device message only: no extra browser line

    def test_browser_flow_is_bounded_and_announced_before_the_wait(self, msal, monkeypatch):
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = _ok()
        shown: list[str] = []

        def announce(msg: str) -> None:
            shown.append(msg)
            msal.calls.append("announce")

        user_signin.login(_profile(home_account_id=None), device_code=False, announce=announce)
        timeout = msal.interactive_kwargs["timeout"]
        assert timeout == 300
        assert len(shown) == 1
        assert "browser" in shown[0]
        assert msal.calls.index("announce") < msal.calls.index("wait")

    @pytest.mark.parametrize("old_msal", [False, True], ids=["msal-1.30+", "msal-before-1.30"])
    def test_browser_timeout_is_a_login_failed_naming_both_causes(
        self, msal, monkeypatch, old_msal
    ):
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        # msal before 1.30 (setup.py allows >=1.20) raised a plain RuntimeError
        raised = RuntimeError if old_msal else msal.timeout_error
        msal.interactive = raised("User did not complete the flow in time")
        with pytest.raises(D365Error) as ei:
            user_signin.login(_profile(), device_code=False)
        assert ei.value.code == "login_failed"
        assert ei.value.status == 401
        text = str(ei.value)
        assert "not finished within" in text
        assert "administrator must grant admin consent" in text
        assert "user_impersonation" in text
        assert "delegated sign-in how-to" in text
        assert "msal: User did not complete the flow in time" in text
        assert token_store.load("dev") == ""

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

    def test_login_timeout_exits_nonzero_with_login_failed(self, msal, monkeypatch):
        self._tty(monkeypatch)
        monkeypatch.setattr(webbrowser, "get", lambda *a: object())
        msal.interactive = msal.timeout_error("User did not complete the flow in time")
        session_mod.save_profile(_profile(home_account_id=None))
        r = CliRunner().invoke(cli, ["profile", "login", "dev"])
        assert r.exit_code == 1, r.output
        # `profile login` refuses --json, so text mode is the only surface; the stable
        # `login_failed` code is asserted on the D365Error in the core test above.
        assert "Sign-in failed" in r.output and "not finished" in r.output
        assert "Traceback" not in r.output
        assert session_mod.load_profile("dev").home_account_id is None

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
        assert r.exit_code == 1
        assert "profile login dev" in r.output

    def test_rm_removes_the_cached_account(self, msal):
        session_mod.save_profile(_profile())
        token_store.save("dev", '{"c": 1}')
        r = CliRunner().invoke(cli, ["profile", "rm", "dev", "--yes"])
        assert r.exit_code == 0, r.output
        assert token_store.load("dev") == "" and not token_store.cache_path("dev").exists()

    def test_rm_survives_a_cache_delete_failure(self, msal, monkeypatch):
        session_mod.save_profile(_profile())

        def boom(name):
            raise PermissionError("read-only")

        monkeypatch.setattr(token_store, "delete", boom)
        r = CliRunner().invoke(cli, ["--json", "profile", "rm", "dev", "--yes"])
        assert r.exit_code == 0, r.output
        assert "read-only" in json.loads(r.stdout)["meta"]["warnings"][0]
        assert "dev" not in session_mod.list_profiles()

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
