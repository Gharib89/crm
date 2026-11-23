"""Command-level tests for `crm connection doctor` + the `crm doctor` alias (#74)."""

# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli

_BASE = "https://internalcrm.contoso.local/Contoso"
_API_BASE = f"{_BASE}/api/data/v9.2/"
_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001"}
_VERSION = {"Version": "9.1.0.1"}


@pytest.fixture(autouse=True)
def _seed_profile(isolated_home):
    """Seed an active NTLM profile + plaintext secret so bare ``connection doctor``
    resolves it (env-derived credentials are gone). Pin api_version to v9.2 so
    api_base is deterministic: the tests register mocks against ``_API_BASE``.
    """
    from crm.core import session as session_mod
    from crm.utils.d365_backend import ConnectionProfile

    session_mod.save_profile(
        ConnectionProfile(
            name="doc", url=_BASE, domain="CONTOSO", username="alice", api_version="v9.2"
        )
    )
    session_mod.save_profile_secret_plaintext("doc", "pw")
    state = session_mod.load_session("default")
    state["active_profile"] = "doc"
    session_mod.save_session(state, "default")
    yield


@pytest.fixture(autouse=True)
def _stub_socket(monkeypatch):
    """The dns_tcp step uses a raw ``socket.create_connection`` that requests_mock
    does NOT intercept, so stub it to a success returning a no-op closeable.
    """

    class _DummySock:
        def close(self):
            pass

    monkeypatch.setattr(
        "crm.core.connection.socket.create_connection",
        lambda *a, **k: _DummySock(),
    )


def _register(m, *, whoami_status=200):
    m.get(_API_BASE, status_code=200, json={})
    m.get(f"{_API_BASE}RetrieveVersion()", json=_VERSION)
    if whoami_status == 200:
        m.get(f"{_API_BASE}WhoAmI", json=_WHOAMI)
    else:
        m.get(
            f"{_API_BASE}WhoAmI",
            status_code=whoami_status,
            json={"error": {"code": "0x0", "message": "Unauthorized"}},
        )


def test_doctor_json_happy_path():
    with requests_mock.Mocker() as m:
        _register(m)
        result = CliRunner().invoke(cli, ["--json", "connection", "doctor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    checks = payload["data"]["checks"]
    assert len(checks) == 5
    assert [c["check"] for c in checks] == [
        "dns_tcp",
        "tls",
        "version",
        "auth",
        "rate_limit",
    ]


def test_doctor_alias_matches_group_command():
    """Proves the top-level alias is wired to the same command object."""
    with requests_mock.Mocker() as m:
        _register(m)
        result = CliRunner().invoke(cli, ["--json", "doctor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["data"]["checks"]) == 5


def test_doctor_json_auth_failure():
    with requests_mock.Mocker() as m:
        _register(m, whoami_status=401)
        result = CliRunner().invoke(cli, ["--json", "connection", "doctor"])
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    auth = next(c for c in payload["data"]["checks"] if c["check"] == "auth")
    assert auth["ok"] is False


def test_doctor_human_failure_renders_checklist():
    """The WHOLE checklist, both the failed ✗ line AND its hint, renders on stdout
    in order, so captured/piped output stays coherent (regression: skin.error
    went to stderr and orphaned each ✗ line from its hint across the two streams).
    """
    with requests_mock.Mocker() as m:
        _register(m, whoami_status=401)
        result = CliRunner().invoke(cli, ["connection", "doctor"])
    assert result.exit_code != 0
    out = result.stdout
    assert "Connection doctor" in out
    assert "dns_tcp" in out
    auth_line = "✗ auth: authentication failed (HTTP 401)"
    hint_line = (
        "check the stored secret — re-store it with `crm profile set-password --profile doc`"
    )
    assert auth_line in out
    assert hint_line in out
    auth_idx = out.index(auth_line)
    hint_idx = out.index(hint_line)
    assert auth_idx < hint_idx
    between = out[auth_idx:hint_idx]
    assert "✓" not in between
    assert "✗" not in between[len("✗ auth: authentication failed (HTTP 401)") :]
    assert auth_line not in result.stderr
