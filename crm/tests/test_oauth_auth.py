"""Unit tests for the OAuth client-credentials auth scheme (issue #49).

msal is always mocked here — these tests never reach AAD. The live WhoAmI
check against a real cloud org is a manual smoke test (see docs).
"""
# pyright: basic
from __future__ import annotations

import sys
import types

import pytest
import requests


def _fake_msal(token_result):
    """A stand-in `msal` module. Returns (module, captured-kwargs dict)."""
    captured = {}
    mod = types.ModuleType("msal")

    class SerializableTokenCache:
        def __init__(self):
            self._state = ""
            self.has_state_changed = False

        def serialize(self):
            return self._state

        def deserialize(self, blob):
            self._state = blob

    class ConfidentialClientApplication:
        def __init__(self, client_id, *, authority=None, client_credential=None, token_cache=None):
            captured["client_id"] = client_id
            captured["authority"] = authority
            captured["client_credential"] = client_credential
            captured["token_cache"] = token_cache

        def acquire_token_for_client(self, scopes=None):
            captured["scopes"] = scopes
            cache = captured.get("token_cache")
            if cache is not None:
                cache.has_state_changed = True
                cache.deserialize('{"token": "x"}')
            return dict(token_result)

    mod.SerializableTokenCache = SerializableTokenCache
    mod.ConfidentialClientApplication = ConfidentialClientApplication
    return mod, captured


def _oauth_profile():
    from crm.utils.d365_backend import ConnectionProfile

    return ConnectionProfile(
        name="t", url="https://contoso.crm.dynamics.com", domain="", username="",
        auth_scheme="oauth", tenant_id="tid", client_id="cid", verify_ssl=False,
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """No .env autoload, no inherited D365_*/CRM_* leaking into oauth tests."""
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))  # authoritative, missing → loads nothing
    for k in (
        "D365_URL", "CRM_BASE_URL", "CRM_URL",
        "D365_USERNAME", "CRM_USERNAME", "CRM_USER",
        "D365_PASSWORD", "CRM_PASSWORD", "CRM_PASS",
        "D365_DOMAIN", "CRM_DOMAIN",
        "D365_AUTH", "CRM_AUTH",
        "D365_TENANT_ID", "CRM_TENANT_ID",
        "D365_CLIENT_ID", "CRM_CLIENT_ID",
        "D365_CLIENT_SECRET", "CRM_CLIENT_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRM_HOME", str(tmp_path / "crmhome"))


class TestProfileFromEnvOAuth:
    def test_oauth_profile_built_without_username(self, monkeypatch):
        from crm.core import connection as conn

        monkeypatch.setenv("D365_URL", "https://contoso.crm.dynamics.com")
        monkeypatch.setenv("D365_AUTH", "oauth")
        monkeypatch.setenv("D365_TENANT_ID", "tid")
        monkeypatch.setenv("D365_CLIENT_ID", "cid")
        # deliberately NO D365_USERNAME / D365_PASSWORD / D365_DOMAIN

        p = conn.profile_from_env()
        assert p.auth_scheme == "oauth"
        assert p.tenant_id == "tid"
        assert p.client_id == "cid"
        assert p.url == "https://contoso.crm.dynamics.com"

    def test_missing_tenant_id_names_the_var(self, monkeypatch):
        from crm.core import connection as conn
        from crm.utils.d365_backend import D365Error

        monkeypatch.setenv("D365_URL", "https://contoso.crm.dynamics.com")
        monkeypatch.setenv("D365_AUTH", "oauth")
        monkeypatch.setenv("D365_CLIENT_ID", "cid")
        # no D365_TENANT_ID
        with pytest.raises(D365Error, match="D365_TENANT_ID"):
            conn.profile_from_env()

    def test_missing_client_id_names_the_var(self, monkeypatch):
        from crm.core import connection as conn
        from crm.utils.d365_backend import D365Error

        monkeypatch.setenv("D365_URL", "https://contoso.crm.dynamics.com")
        monkeypatch.setenv("D365_AUTH", "oauth")
        monkeypatch.setenv("D365_TENANT_ID", "tid")
        # no D365_CLIENT_ID
        with pytest.raises(D365Error, match="D365_CLIENT_ID"):
            conn.profile_from_env()


class TestMakeOAuthAuth:
    def test_oauth_scheme_returns_an_authbase(self, monkeypatch):
        from crm.utils.d365_backend import D365Backend

        mod, _ = _fake_msal({"access_token": "TOK"})
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        assert isinstance(b._session.auth, requests.auth.AuthBase)

    def test_raises_when_msal_missing(self, monkeypatch):
        from crm.utils.d365_backend import D365Backend, D365Error

        monkeypatch.setitem(sys.modules, "msal", None)
        with pytest.raises(D365Error, match="msal"):
            D365Backend(_oauth_profile(), password="secret")

    def test_bearer_injects_authorization_header(self, monkeypatch):
        from crm.utils.d365_backend import D365Backend

        mod, _ = _fake_msal({"access_token": "TOK123"})
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/api/data/v9.2/").prepare()
        b._session.auth(req)
        assert req.headers["Authorization"] == "Bearer TOK123"

    def test_scope_and_authority_derived_from_url_and_tenant(self, monkeypatch):
        from crm.utils.d365_backend import D365Backend

        mod, cap = _fake_msal({"access_token": "TOK"})
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/x").prepare()
        b._session.auth(req)
        assert cap["authority"] == "https://login.microsoftonline.com/tid"
        assert cap["client_id"] == "cid"
        assert cap["client_credential"] == "secret"
        assert cap["scopes"] == ["https://contoso.crm.dynamics.com/.default"]


class TestLazyAppConstruction:
    def test_constructing_backend_does_not_build_msal_app(self, monkeypatch):
        # msal validates the authority over the network when the app is built,
        # so backend construction must NOT build it (parity with the NTLM path).
        from crm.utils.d365_backend import D365Backend

        mod, _ = _fake_msal({"access_token": "TOK"})

        def boom(self, *a, **k):
            raise AssertionError("app built too early")

        mod.ConfidentialClientApplication.__init__ = boom
        monkeypatch.setitem(sys.modules, "msal", mod)
        D365Backend(_oauth_profile(), password="secret")  # must not raise

    def test_app_build_failure_wrapped_as_d365error(self, monkeypatch):
        from crm.utils.d365_backend import D365Backend, D365Error

        mod, _ = _fake_msal({"access_token": "TOK"})

        def boom(self, *a, **k):
            raise ValueError("Unable to get authority configuration")

        mod.ConfidentialClientApplication.__init__ = boom
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/x").prepare()
        with pytest.raises(D365Error, match="(?i)oauth setup failed"):
            b._session.auth(req)


class TestBearerFailure:
    def test_acquire_failure_raises_with_app_registration_guidance(self, monkeypatch):
        from crm.utils.d365_backend import D365Backend, D365Error

        mod, _ = _fake_msal({
            "error": "unauthorized_client",
            "error_description": "AADSTS700016: app not found in tenant",
        })
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/x").prepare()
        with pytest.raises(D365Error, match="(?i)app reg|application user"):
            b._session.auth(req)

    def test_acquire_failure_does_not_retry(self, monkeypatch):
        from crm.utils.d365_backend import D365Backend, D365Error

        calls = {"n": 0}
        mod, _ = _fake_msal({"error": "x"})
        orig = mod.ConfidentialClientApplication.acquire_token_for_client

        def counting(self, scopes=None):
            calls["n"] += 1
            return orig(self, scopes=scopes)

        mod.ConfidentialClientApplication.acquire_token_for_client = counting
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/x").prepare()
        with pytest.raises(D365Error):
            b._session.auth(req)
        assert calls["n"] == 1  # no automatic refresh-retry


class TestTokenCache:
    def test_cache_file_written_at_0600(self, monkeypatch, tmp_path):
        import os
        import stat
        from crm.utils.d365_backend import D365Backend

        home = tmp_path / "h"
        monkeypatch.setenv("CRM_HOME", str(home))
        mod, _ = _fake_msal({"access_token": "TOK"})
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/x").prepare()
        b._session.auth(req)
        cache_file = home / "msal_token_cache.json"
        assert cache_file.exists()
        assert stat.S_IMODE(os.stat(cache_file).st_mode) == 0o600
        assert cache_file.read_text() == '{"token": "x"}'

    def test_cache_reloaded_on_next_construct(self, monkeypatch, tmp_path):
        from crm.utils.d365_backend import D365Backend

        home = tmp_path / "h"
        monkeypatch.setenv("CRM_HOME", str(home))
        mod1, _ = _fake_msal({"access_token": "TOK"})
        monkeypatch.setitem(sys.modules, "msal", mod1)
        b1 = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/x").prepare()
        b1._session.auth(req)  # persists '{"token": "x"}'

        mod2, _ = _fake_msal({"access_token": "TOK"})
        monkeypatch.setitem(sys.modules, "msal", mod2)
        b2 = D365Backend(_oauth_profile(), password="secret")  # second invocation
        # The cache is seeded from disk at construction, so a still-valid token is
        # reused with no AAD round-trip.
        assert b2._session.auth._cache.serialize() == '{"token": "x"}'

    def test_in_memory_fallback_when_home_unwritable(self, monkeypatch, tmp_path):
        from crm.utils.d365_backend import D365Backend

        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setenv("CRM_HOME", str(blocker / "sub"))  # mkdir under a file fails
        mod, _ = _fake_msal({"access_token": "TOK"})
        monkeypatch.setitem(sys.modules, "msal", mod)
        b = D365Backend(_oauth_profile(), password="secret")
        req = requests.Request("GET", "https://contoso.crm.dynamics.com/x").prepare()
        b._session.auth(req)  # must not raise despite unwritable cache path
        assert req.headers["Authorization"] == "Bearer TOK"
        assert not (blocker / "sub").exists()


class TestResolveCredentialsOAuth:
    def _set_oauth_env(self, monkeypatch):
        monkeypatch.setenv("D365_URL", "https://contoso.crm.dynamics.com")
        monkeypatch.setenv("D365_AUTH", "oauth")
        monkeypatch.setenv("D365_TENANT_ID", "tid")
        monkeypatch.setenv("D365_CLIENT_ID", "cid")

    def test_client_secret_flows_as_password(self, monkeypatch):
        from crm.core import connection as conn

        self._set_oauth_env(monkeypatch)
        monkeypatch.setenv("D365_CLIENT_SECRET", "sssh-secret")
        resolved = conn.resolve_credentials()
        assert resolved.profile.auth_scheme == "oauth"
        assert resolved.password == "sssh-secret"

    def test_missing_client_secret_names_the_var(self, monkeypatch):
        from crm.core import connection as conn
        from crm.utils.d365_backend import D365Error

        self._set_oauth_env(monkeypatch)
        # no D365_CLIENT_SECRET, no D365_PASSWORD
        with pytest.raises(D365Error, match="D365_CLIENT_SECRET"):
            conn.resolve_credentials()


class TestProfileAcceptsOAuth:
    def test_oauth_scheme_is_accepted(self):
        from crm.utils.d365_backend import ConnectionProfile

        p = ConnectionProfile(
            name="t", url="https://contoso.crm.dynamics.com", domain="",
            username="", auth_scheme="oauth",
        )
        assert p.auth_scheme == "oauth"

    def test_tenant_and_client_round_trip_through_dict(self):
        from crm.utils.d365_backend import ConnectionProfile

        p = ConnectionProfile(
            name="t", url="https://contoso.crm.dynamics.com", domain="",
            username="", auth_scheme="oauth",
            tenant_id="11111111-1111-1111-1111-111111111111",
            client_id="22222222-2222-2222-2222-222222222222",
        )
        d = p.to_dict()
        assert d["tenant_id"] == "11111111-1111-1111-1111-111111111111"
        assert d["client_id"] == "22222222-2222-2222-2222-222222222222"
        assert "client_secret" not in d  # secret never persisted on the profile
        back = ConnectionProfile.from_dict(d)
        assert back.tenant_id == p.tenant_id
        assert back.client_id == p.client_id

    def test_tenant_and_client_default_to_none(self):
        from crm.utils.d365_backend import ConnectionProfile

        p = ConnectionProfile(name="t", url="https://crm/o", domain="D", username="u")
        assert p.tenant_id is None
        assert p.client_id is None
