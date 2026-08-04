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
        name=name,
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        **kw,
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
    multi-profile debugging session doesn't have to hunt for which one is broken.
    """

    def test_missing_url_names_the_profile(self):
        profile = ConnectionProfile(name="brokenprod", url="", domain="CONTOSO", username="alice")
        with pytest.raises(D365Error, match="brokenprod") as exc:
            D365Backend(profile, "pw")
        assert "server URL" in str(exc.value)

    def test_missing_username_names_the_profile(self):
        profile = ConnectionProfile(
            name="brokenntlm",
            url="https://crm.contoso.local/c",
            domain="CONTOSO",
            username="",
            auth_scheme="ntlm",
        )
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
        monkeypatch.setattr(
            conn_mod.keyring_store, "set_secret", lambda n, s: stored.__setitem__(n, s)
        )
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


class TestCallerUiLanguage:
    """caller_ui_language_id resolves the caller's uilanguageid, caches it, and
    degrades to None on any lookup failure (#940).
    """

    _UID = "20fdfe32-497b-f111-ab0e-7c1e528d4ca5"

    def _mock(self, m, backend, *, who=None, settings=None, who_status=200, settings_status=200):
        import requests_mock  # noqa: F401  (ensure the dependency is present)

        who_kw = {"json": who} if who is not None else {"status_code": who_status}
        set_kw = {"json": settings} if settings is not None else {"status_code": settings_status}
        who_matcher = m.get(backend.url_for("WhoAmI"), **who_kw)
        set_matcher = m.get(backend.url_for(f"usersettingscollection({self._UID})"), **set_kw)
        return who_matcher, set_matcher

    def test_resolves_uilanguageid_from_usersettings(self, backend):
        import requests_mock

        with requests_mock.Mocker() as m:
            self._mock(m, backend, who={"UserId": self._UID}, settings={"uilanguageid": 1025})
            assert conn_mod.caller_ui_language_id(backend) == 1025

    def test_caches_after_first_lookup(self, backend):
        import requests_mock

        with requests_mock.Mocker() as m:
            who, sett = self._mock(
                m, backend, who={"UserId": self._UID}, settings={"uilanguageid": 1033}
            )
            assert conn_mod.caller_ui_language_id(backend) == 1033
            assert conn_mod.caller_ui_language_id(backend) == 1033
        assert who.call_count == 1
        assert sett.call_count == 1

    def test_none_when_whoami_fails(self, backend):
        import requests_mock

        with requests_mock.Mocker() as m:
            self._mock(m, backend, who_status=500)
            assert conn_mod.caller_ui_language_id(backend) is None

    def test_none_when_uilanguageid_absent(self, backend):
        import requests_mock

        with requests_mock.Mocker() as m:
            self._mock(m, backend, who={"UserId": self._UID}, settings={"systemuserid": self._UID})
            assert conn_mod.caller_ui_language_id(backend) is None
