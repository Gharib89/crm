"""resolve_credentials: keyring / plaintext / prompt steps (issue #130)."""
# pyright: basic
from __future__ import annotations

import os

import pytest

from crm.core import connection as conn_mod
from crm.core import keyring_store
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    for k in ("D365_PASSWORD", "CRM_PASSWORD", "D365_CLIENT_SECRET", "CRM_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory keyring_store: patch the funcs the resolver calls."""
    store: dict[str, str] = {}
    monkeypatch.setattr(keyring_store, "is_available", lambda: True)
    monkeypatch.setattr(keyring_store, "get_secret", lambda n: store.get(n))
    return store


def _save(name="prod"):
    session_mod.save_profile(ConnectionProfile(
        name=name, url="https://crm.contoso.local/c", domain="C", username="alice",
    ))


def test_override_beats_everything(fake_keyring, monkeypatch):
    _save()
    fake_keyring["prod"] = "from-keyring"
    monkeypatch.setenv("D365_PASSWORD", "from-env")
    rc = conn_mod.resolve_credentials("prod", password_override="from-flag")
    assert rc.password == "from-flag"


def test_env_beats_keyring(fake_keyring, monkeypatch):
    _save()
    fake_keyring["prod"] = "from-keyring"
    monkeypatch.setenv("D365_PASSWORD", "from-env")
    rc = conn_mod.resolve_credentials("prod")
    assert rc.password == "from-env"


def test_keyring_used_when_no_flag_or_env(fake_keyring):
    _save()
    fake_keyring["prod"] = "from-keyring"
    rc = conn_mod.resolve_credentials("prod")
    assert rc.password == "from-keyring"


def test_plaintext_beats_keyring(fake_keyring):
    _save()
    fake_keyring["prod"] = "from-keyring"
    session_mod.save_profile_secret_plaintext("prod", "from-disk")
    rc = conn_mod.resolve_credentials("prod")
    assert rc.password == "from-disk"


def test_prompt_when_allowed_and_nothing_else(monkeypatch):
    _save()
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "typed-secret")
    rc = conn_mod.resolve_credentials("prod", allow_prompt=True)
    assert rc.password == "typed-secret"


def test_raise_when_nothing_and_no_prompt(monkeypatch):
    _save()
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)
    with pytest.raises(D365Error, match="keyring|--store-password"):
        conn_mod.resolve_credentials("prod", allow_prompt=False)


def test_oauth_resolves_client_secret_from_keyring(fake_keyring):
    # oauth profiles resolve the CLIENT SECRET (not a password) from the same
    # on-disk chain; here the OS keyring supplies it (#130).
    session_mod.save_profile(ConnectionProfile(
        name="cloud", url="https://contoso.crm.dynamics.com/x", domain="",
        username="", auth_scheme="oauth", tenant_id="t", client_id="c",
    ))
    fake_keyring["cloud"] = "oauth-secret"
    rc = conn_mod.resolve_credentials("cloud")
    assert rc.password == "oauth-secret"
