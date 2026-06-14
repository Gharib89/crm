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

    def test_unrelated_status_has_no_hint(self):
        assert _auth_error_hint(404, "cloud") == ""


import pytest
from crm.commands._helpers import select_one


class TestSelectOne:
    def test_non_tty_raises_runtime_error(self, monkeypatch):
        # No TTY -> the picker must refuse rather than block on input.
        monkeypatch.setattr("crm.commands._helpers.confirm._stdin_is_tty", lambda: False)
        with pytest.raises(RuntimeError, match="no interactive terminal"):
            select_one("Pick one", [("a", "label a"), ("b", "label b")])

    def test_empty_items_raises_value_error(self, monkeypatch):
        monkeypatch.setattr("crm.commands._helpers.confirm._stdin_is_tty", lambda: True)
        with pytest.raises(ValueError, match="no choices"):
            select_one("Pick one", [])

    def test_returns_selected_value(self, monkeypatch):
        monkeypatch.setattr("crm.commands._helpers.confirm._stdin_is_tty", lambda: True)
        # Stub questionary.select so the test never opens a real TUI.
        class _FakeSelect:
            def ask(self):
                return "b"
        # select_one imports questionary lazily (kept off the fast-startup
        # path), so patch select at the source module.
        monkeypatch.setattr("questionary.select", lambda *a, **kw: _FakeSelect())
        assert select_one("Pick one", [("a", "label a"), ("b", "label b")]) == "b"

    def test_cancel_returns_none(self, monkeypatch):
        # questionary.select.ask() returns None on Esc / Ctrl-C.
        monkeypatch.setattr("crm.commands._helpers.confirm._stdin_is_tty", lambda: True)
        class _FakeSelect:
            def ask(self):
                return None
        monkeypatch.setattr("questionary.select", lambda *a, **kw: _FakeSelect())
        assert select_one("Pick one", [("a", "label a")]) is None

    def test_default_not_among_choices_raises_value_error(self, monkeypatch):
        # A default that matches no item value is a contract violation — fail
        # loudly rather than silently dropping preselection.
        monkeypatch.setattr("crm.commands._helpers.confirm._stdin_is_tty", lambda: True)
        with pytest.raises(ValueError, match="not among the choices"):
            select_one("Pick one", [("a", "label a"), ("b", "label b")], default="z")

    def test_default_preselects_matching_choice(self, monkeypatch):
        # The optional default is forwarded to questionary.select so the wizard
        # can preselect the URL-inferred scheme; choices keep (value, label).
        monkeypatch.setattr("crm.commands._helpers.confirm._stdin_is_tty", lambda: True)
        captured = {}
        class _FakeSelect:
            def ask(self):
                return "oauth"
        def _fake_select(message, choices=None, default=None, **kw):
            captured["default"] = default
            captured["values"] = [c.value for c in (choices or [])]
            return _FakeSelect()
        monkeypatch.setattr("questionary.select", _fake_select)
        out = select_one("Auth scheme", [("ntlm", "ntlm"), ("oauth", "oauth")],
                         default="oauth")
        assert out == "oauth"
        assert captured["default"] == "oauth"
        assert captured["values"] == ["ntlm", "oauth"]
