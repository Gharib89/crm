"""Unit tests for profile-UX pure helpers."""
# pyright: basic
from __future__ import annotations

from crm.commands._helpers import (
    infer_auth_scheme,
    default_profile_name,
    _auth_error_hint,
)


class TestInferAuthScheme:
    def test_dynamics_host_is_oauth(self):
        assert infer_auth_scheme("https://org.crm.dynamics.com") == "oauth"

    def test_dynamics_regional_host_is_oauth(self):
        assert infer_auth_scheme("https://org.crm4.dynamics.com/") == "oauth"

    def test_onprem_host_is_ntlm(self):
        assert infer_auth_scheme("https://crm.contoso.local/contoso") == "ntlm"

    def test_blank_url_defaults_ntlm(self):
        assert infer_auth_scheme("") == "ntlm"


class TestDefaultProfileName:
    def test_uses_first_host_label(self):
        assert default_profile_name("https://crm.contoso.local/contoso") == "crm"

    def test_dynamics_uses_org_label(self):
        assert default_profile_name("https://orgd080.crm.dynamics.com") == "orgd080"

    def test_falls_back_to_default_when_unparseable(self):
        assert default_profile_name("not-a-url") == "default"

    def test_blank_falls_back_to_default(self):
        assert default_profile_name("") == "default"


class TestAuthErrorHint:
    def test_401_hints_set_password(self):
        hint = _auth_error_hint(401, "cloud")
        assert "crm profile set-password" in hint
        assert "--profile cloud" in hint

    def test_no_secret_message_hints_set_password(self):
        hint = _auth_error_hint(None, "cloud", no_secret=True)
        assert "crm profile set-password" in hint

    def test_unrelated_status_has_no_hint(self):
        assert _auth_error_hint(404, "cloud") == ""
