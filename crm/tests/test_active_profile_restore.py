"""backend() restores session active_profile across invocations (issue #130)."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import keyring_store
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile

_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001"}
_BASE = "https://crm.contoso.local/Contoso"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    for k in ("D365_URL", "CRM_BASE_URL", "D365_PASSWORD", "CRM_PASSWORD"):
        os.environ.pop(k, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(keyring_store, "is_available", lambda: True)
    monkeypatch.setattr(keyring_store, "get_secret", lambda n: store.get(n))
    monkeypatch.setattr(keyring_store, "set_secret",
                        lambda n, s: store.__setitem__(n, s))
    monkeypatch.setattr(keyring_store, "has_secret", lambda n: n in store)
    return store


def _seed_active_profile(name="prod"):
    """Save a profile + activate it in the session, mimicking `crm profile add`."""
    session_mod.save_profile(ConnectionProfile(
        name=name, url=_BASE, domain="CONTOSO", username="alice",
        api_version="v9.2",
    ))
    state = session_mod.load_session("default")
    state["active_profile"] = name
    session_mod.save_session(state, "default")


def test_whoami_uses_active_profile_without_flag(fake_keyring):
    # A profile saved + activated, with its secret in the keyring, must be used
    # by a fresh invocation that passes NO --profile, NO env, NO --password.
    _seed_active_profile("prod")
    fake_keyring["prod"] = "pw"
    runner = CliRunner()
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
        r = runner.invoke(cli, ["--json", "connection", "whoami"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["data"]["UserId"] == _WHOAMI["UserId"]


def test_stale_active_profile_errors_cleanly(fake_keyring):
    # active_profile points at a profile whose file does NOT exist. There is no
    # env fallback any more: the run must exit 1 with a clean error envelope
    # (no crash, no traceback), steering the user to `crm profile add`.
    state = session_mod.load_session("default")
    state["active_profile"] = "ghost"
    session_mod.save_session(state, "default")
    r = CliRunner().invoke(cli, ["--json", "connection", "whoami"])
    assert r.exit_code == 1, r.output
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert "Traceback" not in (r.stdout + (r.stderr or ""))
    # The stale pointer is ignored (file missing) → resolve_credentials(None)
    # raises the "No profile configured" guidance.
    assert "profile" in payload["error"].lower()
