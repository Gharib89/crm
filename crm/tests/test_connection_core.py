"""Core credential resolution + storage after the env/.env removal."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core import connection as conn_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    return tmp_path


def _save(name="contoso", **kw):
    p = ConnectionProfile(
        name=name, url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", **kw,
    )
    session_mod.save_profile(p)
    return p


class TestResolveCredentials:
    def test_password_override_wins(self, crm_home):
        _save()
        r = conn_mod.resolve_credentials("contoso", password_override="pw")
        assert r.password == "pw"
        assert r.profile.name == "contoso"

    def test_reads_plaintext_secret(self, crm_home):
        _save()
        session_mod.save_profile_secret_plaintext("contoso", "fromfile")
        r = conn_mod.resolve_credentials("contoso")
        assert r.password == "fromfile"

    def test_missing_profile_raises(self, crm_home):
        with pytest.raises(D365Error, match="not found"):
            conn_mod.resolve_credentials("ghost")

    def test_no_profile_name_raises(self, crm_home):
        # Env-derived profiles are gone: a None profile name is now an error.
        with pytest.raises(D365Error, match="No profile"):
            conn_mod.resolve_credentials(None)

    def test_no_secret_raises_with_actionable_message(self, crm_home):
        _save()
        with pytest.raises(D365Error, match="set-password"):
            conn_mod.resolve_credentials("contoso", allow_prompt=False)

    def test_prompt_uses_masked_input_when_allowed(self, crm_home, monkeypatch):
        # allow_prompt (== TTY and not --json, set by cli.py) reads the secret
        # via the masked questionary prompt (echoes '*'), not fully-hidden
        # getpass (#655).
        _save()
        class _FakePw:
            def ask(self):
                return "prompted-secret"
        monkeypatch.setattr("questionary.password", lambda *a, **kw: _FakePw())
        r = conn_mod.resolve_credentials("contoso", allow_prompt=True)
        assert r.password == "prompted-secret"


class TestBackendConstructionValidation:
    """The backend's constructor validation names the offending profile, so a
    multi-profile debugging session doesn't have to hunt for which one is broken."""

    def test_missing_url_names_the_profile(self):
        profile = ConnectionProfile(
            name="brokenprod", url="", domain="CONTOSO", username="alice")
        with pytest.raises(D365Error, match="brokenprod") as exc:
            D365Backend(profile, "pw")
        assert "server URL" in str(exc.value)

    def test_missing_username_names_the_profile(self):
        profile = ConnectionProfile(
            name="brokenntlm", url="https://crm.contoso.local/c",
            domain="CONTOSO", username="", auth_scheme="ntlm")
        with pytest.raises(D365Error, match="brokenntlm") as exc:
            D365Backend(profile, "pw")
        assert "username" in str(exc.value)


class TestSaveSecret:
    def test_keyring_unavailable_falls_back_to_plaintext(self, crm_home, monkeypatch):
        _save()
        monkeypatch.setattr(conn_mod.keyring_store, "is_available", lambda: False)
        where = conn_mod.save_secret("contoso", "sekret")
        assert where == "plaintext"
        assert session_mod.load_profile_secret("contoso") == "sekret"

    def test_keyring_available_uses_keyring(self, crm_home, monkeypatch):
        _save()
        stored = {}
        monkeypatch.setattr(conn_mod.keyring_store, "is_available", lambda: True)
        monkeypatch.setattr(conn_mod.keyring_store, "set_secret",
                            lambda n, s: stored.__setitem__(n, s))
        monkeypatch.setattr(conn_mod.keyring_store, "delete_secret", lambda n: False)
        where = conn_mod.save_secret("contoso", "sekret")
        assert where == "keyring"
        assert stored["contoso"] == "sekret"
        # keyring path must clear any stale plaintext (single-store invariant)
        assert session_mod.load_profile_secret("contoso") is None

    def test_force_plaintext_skips_keyring(self, crm_home, monkeypatch):
        _save()
        monkeypatch.setattr(conn_mod.keyring_store, "is_available", lambda: True)
        monkeypatch.setattr(conn_mod.keyring_store, "delete_secret", lambda n: False)
        where = conn_mod.save_secret("contoso", "sekret", force_plaintext=True)
        assert where == "plaintext"
        assert session_mod.load_profile_secret("contoso") == "sekret"
