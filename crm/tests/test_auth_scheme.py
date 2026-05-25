"""Unit tests for --auth-scheme + Kerberos support."""
# pyright: basic
from __future__ import annotations

import sys

import pytest

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


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

    def test_kerberos_scheme_raises_when_package_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "requests_negotiate_sspi", None)
        with pytest.raises(D365Error, match="requests_negotiate_sspi"):
            D365Backend(_profile("kerberos"), password="pw")

    def test_negotiate_scheme_raises_when_package_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "requests_negotiate_sspi", None)
        with pytest.raises(D365Error, match="requests_negotiate_sspi"):
            D365Backend(_profile("negotiate"), password="pw")

    def test_unknown_scheme_raises(self):
        with pytest.raises(D365Error, match="auth_scheme"):
            D365Backend(_profile("oauth2"), password="pw")
