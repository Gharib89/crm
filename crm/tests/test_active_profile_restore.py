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


def test_whoami_uses_active_profile_without_flag(fake_keyring):
    runner = CliRunner()
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
        # connect stores in keyring AND sets the session active_profile.
        r1 = runner.invoke(cli, [
            "connection", "connect", "--url", _BASE, "--username", "alice",
            "--domain", "CONTOSO", "--password", "pw",
            "--profile-name", "prod", "--store-password",
        ])
        assert r1.exit_code == 0, r1.output
        # Fresh invocation: NO --profile, NO env, NO --password. Must work via
        # restored active_profile + keyring secret.
        r2 = runner.invoke(cli, ["--json", "connection", "whoami"])
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.stdout)["data"]["UserId"] == _WHOAMI["UserId"]


def test_stale_active_profile_falls_back_to_env(fake_keyring, monkeypatch):
    # active_profile points at a deleted profile → fall back to env, no crash.
    runner = CliRunner()
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
        runner.invoke(cli, [
            "connection", "connect", "--url", _BASE, "--username", "alice",
            "--domain", "CONTOSO", "--password", "pw",
            "--profile-name", "prod", "--store-password",
        ])
        # Delete the profile file but leave active_profile pointing at it.
        os.remove(os.path.join(os.environ["CRM_HOME"], "profiles", "prod.json"))
        monkeypatch.setenv("D365_URL", _BASE)
        monkeypatch.setenv("D365_USERNAME", "bob")
        monkeypatch.setenv("D365_PASSWORD", "envpw")
        r = runner.invoke(cli, ["--json", "connection", "whoami"])
    assert r.exit_code == 0, r.output  # used env, did not crash on stale pointer
