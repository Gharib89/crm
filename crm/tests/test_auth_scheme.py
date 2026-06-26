"""Unit tests for --auth-scheme + Kerberos support."""
# pyright: basic
from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error

_BAD_CHOICE = "Invalid value for '--auth-scheme'"


def _profile(scheme: str = "ntlm") -> ConnectionProfile:
    return ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice",
        verify_ssl=False, auth_scheme=scheme,
    )


class TestProfileField:
    def test_default_auth_scheme_is_ntlm(self):
        p = ConnectionProfile(
            name="t", url="https://crm/test", domain="D", username="u",
        )
        assert p.auth_scheme == "ntlm"

    def test_profile_to_dict_includes_auth_scheme(self):
        p = _profile("kerberos")
        d = p.to_dict()
        assert d["auth_scheme"] == "kerberos"

    def test_profile_from_dict_defaults_to_ntlm_when_missing(self):
        p = ConnectionProfile.from_dict({
            "name": "t", "url": "https://crm/test",
            "domain": "D", "username": "u",
        })
        assert p.auth_scheme == "ntlm"


class TestAuthSelection:
    def test_ntlm_scheme_uses_ntlm_auth(self):
        from requests_ntlm import HttpNtlmAuth
        b = D365Backend(_profile("ntlm"), password="pw")
        assert isinstance(b._session.auth, HttpNtlmAuth)

    @pytest.mark.parametrize("scheme", ["kerberos", "negotiate"])
    def test_kerberos_scheme_raises_when_package_missing(self, scheme, monkeypatch):
        monkeypatch.setitem(sys.modules, "requests_negotiate_sspi", None)
        with pytest.raises(D365Error, match="requests_negotiate_sspi"):
            D365Backend(_profile(scheme), password="pw")

    def test_unknown_scheme_raises(self):
        with pytest.raises(D365Error, match="auth_scheme"):
            D365Backend(_profile("oauth2"), password="pw")

    def test_missing_requests_raises_clean_d365error(self, monkeypatch):
        # requests is now imported lazily at construction (#247). A broken/partial
        # install (requests absent) must surface as a clean D365Error — same as the
        # pre-deferral lazy command loader did — not a raw ImportError traceback.
        monkeypatch.setitem(sys.modules, "requests", None)
        with pytest.raises(D365Error, match="requests"):
            D365Backend(_profile("ntlm"), password="pw")


class TestAuthSchemeFlag:
    """The global --auth-scheme flag must accept every backend-valid scheme."""

    @pytest.mark.parametrize("scheme", ["ntlm", "kerberos", "negotiate", "oauth"])
    def test_flag_accepts_valid_scheme(self, scheme):
        result = CliRunner().invoke(cli, ["--auth-scheme", scheme, "session", "info"])
        assert _BAD_CHOICE not in result.output

    def test_flag_rejects_unknown_scheme(self):
        result = CliRunner().invoke(cli, ["--auth-scheme", "bogus", "session", "info"])
        assert result.exit_code == 2
        assert _BAD_CHOICE in result.output
