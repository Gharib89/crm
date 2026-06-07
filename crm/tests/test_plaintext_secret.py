"""Plaintext profile-secret helpers (issue #130, Approach B)."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest

from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))


def _save_base_profile(name="prod"):
    session_mod.save_profile(ConnectionProfile(
        name=name, url="https://crm.contoso.local/c", domain="C", username="alice",
    ))


def test_load_secret_none_when_absent():
    _save_base_profile()
    assert session_mod.load_profile_secret("prod") is None


def test_save_then_load_roundtrips():
    _save_base_profile()
    session_mod.save_profile_secret_plaintext("prod", "p@ss")
    assert session_mod.load_profile_secret("prod") == "p@ss"


def test_secret_not_in_dataclass_roundtrip():
    # Approach B: the secret lives only as the _secret JSON key, never on the
    # dataclass — so to_dict()/status output can't leak it.
    _save_base_profile()
    session_mod.save_profile_secret_plaintext("prod", "p@ss")
    p = session_mod.load_profile("prod")
    assert "_secret" not in p.to_dict()
    assert "p@ss" not in json.dumps(p.to_dict())


def test_clear_removes_secret():
    _save_base_profile()
    session_mod.save_profile_secret_plaintext("prod", "p@ss")
    assert session_mod.clear_profile_secret("prod") is True
    assert session_mod.load_profile_secret("prod") is None
    # Profile itself survives the clear.
    assert session_mod.load_profile("prod").username == "alice"


def test_clear_noop_returns_false():
    _save_base_profile()
    assert session_mod.clear_profile_secret("prod") is False


@pytest.mark.skipif(os.name != "posix", reason="chmod 0600 only enforced on POSIX")
def test_plaintext_file_is_0600():
    _save_base_profile()
    path = session_mod.save_profile_secret_plaintext("prod", "p@ss")
    assert (path.stat().st_mode & 0o777) == 0o600
