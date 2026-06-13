"""Offline unit tests for the e2e suite's target/credential selection helpers.

These exercise the pure helpers in ``crm/tests/e2e/conftest.py`` (profile-vs-env
credential sourcing, opt-in gating, and reachability classification) WITHOUT a
live server. The file lives OUTSIDE ``crm/tests/e2e/`` so it is not auto-marked
``e2e`` and runs in the normal offline CI.
"""
# pyright: basic
from __future__ import annotations

import pytest

from crm.tests.e2e import conftest as e2e
# The prefix the backend stamps on a wrapped transport failure (DNS/TCP/TLS/
# timeout) — import the real constant so this test tracks any rename.
from crm.utils.d365_backend import D365Error, _TRANSPORT_FAILURE_PREFIX


class TestReachabilityClassification:
    """`_is_unreachable` — a host that never answers is unreachable; any HTTP
    response (even an auth/server error) is reachable."""

    def test_status_less_transport_failure_is_unreachable(self):
        exc = D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: Connection refused", status=None)
        assert e2e._is_unreachable(exc) is True

    def test_http_401_is_reachable(self):
        assert e2e._is_unreachable(D365Error("Unauthorized", status=401)) is False

    def test_http_403_is_reachable(self):
        assert e2e._is_unreachable(D365Error("Forbidden", status=403)) is False

    def test_status_less_non_transport_error_is_reachable(self):
        # A client-side validation D365Error carries no status but is NOT a
        # connectivity failure — must not be masked as "unreachable".
        assert e2e._is_unreachable(D365Error("etag must be non-empty", status=None)) is False

    def test_unrelated_exception_is_reachable(self):
        # A status-less error that is NOT a transport failure must not be masked
        # as unreachable.
        assert e2e._is_unreachable(ValueError("boom")) is False


# Reference the conftest's individual cred-var-name constants (not a literal
# tuple of credential-looking strings — that trips GitGuardian's Authentication
# Tuple detector). Plus the auth/domain selectors, which aren't credentials.
_ENV_CRED_VARS = (
    e2e._E_URL, e2e._E_USERNAME, e2e._E_PW,
    e2e._E_CLIENT_ID, e2e._E_CLIENT_SECRET, e2e._E_TENANT_ID,
    "D365_AUTH", "D365_DOMAIN",
)


@pytest.fixture
def clean_e2e_env(monkeypatch):
    """Strip every D365_* / opt-in var so each test sets only what it asserts on."""
    for var in (*_ENV_CRED_VARS, "D365_E2E", e2e._E2E_PROFILE_ENV):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestOptIn:
    """`_e2e_opted_in` — D365_E2E=1 plus EITHER a named profile OR a full env
    credential set."""

    def test_not_opted_in_without_d365_e2e(self, clean_e2e_env):
        clean_e2e_env.setenv(e2e._E2E_PROFILE_ENV, "anyprofile")
        assert e2e._e2e_opted_in() is False

    def test_profile_var_opts_in_without_env_creds(self, clean_e2e_env):
        clean_e2e_env.setenv("D365_E2E", "1")
        clean_e2e_env.setenv(e2e._E2E_PROFILE_ENV, "anyprofile")
        # No D365_URL/USERNAME/PASSWORD set — the profile supplies everything.
        assert e2e._e2e_opted_in() is True

    def test_ntlm_env_set_opts_in(self, clean_e2e_env):
        clean_e2e_env.setenv("D365_E2E", "1")
        clean_e2e_env.setenv(e2e._E_URL, "http://crm.contoso.local/Contoso")
        clean_e2e_env.setenv(e2e._E_USERNAME, "contoso\\admin")
        clean_e2e_env.setenv(e2e._E_PW, "pw")
        assert e2e._e2e_opted_in() is True

    def test_incomplete_env_without_profile_not_opted_in(self, clean_e2e_env):
        clean_e2e_env.setenv("D365_E2E", "1")
        clean_e2e_env.setenv(e2e._E_URL, "http://crm.contoso.local/Contoso")
        # Missing username/password and no profile var.
        assert e2e._e2e_opted_in() is False


def _seed_profile(home, monkeypatch, *, name, url, auth_scheme,
                  secret="pw", domain="", username="", **kw):
    """Save a real profile + plaintext secret under an isolated CRM_HOME, the way
    `crm profile add` would, so `_resolve_e2e_profile` can load it back."""
    from crm.core import session as session_mod
    from crm.utils.d365_backend import ConnectionProfile

    monkeypatch.setenv("CRM_HOME", str(home))
    profile = ConnectionProfile(
        name=name, url=url, domain=domain, username=username,
        auth_scheme=auth_scheme, **kw,
    )
    session_mod.save_profile(profile)
    session_mod.save_profile_secret_plaintext(name, secret)
    return profile


class TestResolveEnvPath:
    """`_resolve_e2e_profile` with D365_E2E_PROFILE unset — builds the throwaway
    profile from the flat D365_* env set (the unchanged CI path)."""

    def test_ntlm_env_builds_onprem_profile(self, clean_e2e_env):
        clean_e2e_env.setenv(e2e._E_URL, "http://crm.contoso.local/Contoso")
        clean_e2e_env.setenv(e2e._E_USERNAME, "contoso\\admin")
        clean_e2e_env.setenv("D365_DOMAIN", "contoso")
        clean_e2e_env.setenv(e2e._E_PW, "pw123")
        profile, secret = e2e._resolve_e2e_profile()
        assert profile.name == e2e._LIVE_PROFILE
        assert profile.auth_scheme == "ntlm"
        assert profile.url == "http://crm.contoso.local/Contoso"
        assert profile.username == "contoso\\admin"
        assert profile.api_version == "v9.1"
        assert secret == "pw123"

    def test_oauth_env_builds_cloud_profile(self, clean_e2e_env):
        clean_e2e_env.setenv("D365_AUTH", "oauth")
        clean_e2e_env.setenv(e2e._E_URL, "https://org.example.crm.local")
        clean_e2e_env.setenv(e2e._E_CLIENT_ID, "client-abc")
        clean_e2e_env.setenv(e2e._E_TENANT_ID, "tenant-xyz")
        clean_e2e_env.setenv(e2e._E_CLIENT_SECRET, "sk")
        profile, secret = e2e._resolve_e2e_profile()
        assert profile.name == e2e._LIVE_PROFILE
        assert profile.auth_scheme == "oauth"
        assert profile.username == ""  # OAuth has no username
        assert profile.tenant_id == "tenant-xyz"
        assert profile.client_id == "client-abc"
        assert profile.api_version == "v9.2"
        assert secret == "sk"


class TestResolveProfilePath:
    """`_resolve_e2e_profile` with D365_E2E_PROFILE set — loads the named profile
    + secret from the real CRM_HOME and renames a copy to the throwaway profile.
    Target is intrinsic to the loaded profile's auth scheme."""

    def test_cloud_profile_loaded_and_renamed(self, tmp_path, clean_e2e_env):
        _seed_profile(
            tmp_path, clean_e2e_env, name="mycloud",
            url="https://org.example.crm.local", auth_scheme="oauth",
            secret="pw-cloud", tenant_id="t1", client_id="c1",
        )
        clean_e2e_env.setenv(e2e._E2E_PROFILE_ENV, "mycloud")
        profile, secret = e2e._resolve_e2e_profile()
        assert profile.name == e2e._LIVE_PROFILE         # renamed for the throwaway
        assert profile.auth_scheme == "oauth"            # → target == cloud
        assert profile.url == "https://org.example.crm.local"
        assert profile.client_id == "c1"
        assert secret == "pw-cloud"

    def test_onprem_profile_loaded_and_renamed(self, tmp_path, clean_e2e_env):
        _seed_profile(
            tmp_path, clean_e2e_env, name="myonprem",
            url="http://crm.contoso.local/Contoso", auth_scheme="ntlm",
            secret="pw-np", username="contoso\\admin",
        )
        clean_e2e_env.setenv(e2e._E2E_PROFILE_ENV, "myonprem")
        profile, secret = e2e._resolve_e2e_profile()
        assert profile.name == e2e._LIVE_PROFILE
        assert profile.auth_scheme == "ntlm"             # → target == onprem
        assert secret == "pw-np"

    def test_missing_profile_raises_naming_the_var(self, tmp_path, clean_e2e_env):
        clean_e2e_env.setenv("CRM_HOME", str(tmp_path))
        clean_e2e_env.setenv(e2e._E2E_PROFILE_ENV, "ghost")
        with pytest.raises(D365Error) as ei:
            e2e._resolve_e2e_profile()
        assert "ghost" in str(ei.value)

    def test_profile_without_secret_raises(self, tmp_path, clean_e2e_env):
        from crm.core import session as session_mod
        from crm.utils.d365_backend import ConnectionProfile
        clean_e2e_env.setenv("CRM_HOME", str(tmp_path))
        session_mod.save_profile(ConnectionProfile(
            name="secretless", url="http://crm.contoso.local/Contoso",
            domain="contoso", username="contoso\\admin", auth_scheme="ntlm",
        ))
        clean_e2e_env.setenv(e2e._E2E_PROFILE_ENV, "secretless")
        with pytest.raises(D365Error):
            e2e._resolve_e2e_profile()
