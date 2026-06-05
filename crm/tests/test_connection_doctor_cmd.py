"""Command-level tests for `crm connection doctor` + the `crm doctor` alias (#74)."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli

_BASE = "https://internalcrm.contoso.local/Contoso"
_API_BASE = f"{_BASE}/api/data/v9.2/"
_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001"}
_VERSION = {"Version": "9.1.0.1"}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    # Snapshot/restore os.environ around each test: ctx.backend() →
    # resolve_credentials → load_dotenv() writes .env values straight into
    # os.environ (monkeypatch can't undo those), which would leak into later
    # tests (cf. #56). Default CRM_DOTENV to a noop path so the repo's real
    # ./.env is never autoloaded. Env creds make ctx.backend() build from env
    # with no saved profile, pinned to v9.2 so api_base is deterministic.
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    os.environ["D365_URL"] = _BASE
    os.environ["D365_USERNAME"] = "alice"
    os.environ["D365_PASSWORD"] = "pw"
    os.environ["D365_DOMAIN"] = "CONTOSO"
    os.environ["D365_AUTH"] = "ntlm"
    os.environ["D365_API_VERSION"] = "v9.2"
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _stub_socket(monkeypatch):
    # The dns_tcp step uses a raw socket.create_connection that requests_mock
    # does NOT intercept — stub it to a success returning a no-op closeable.
    class _DummySock:
        def close(self):
            pass

    monkeypatch.setattr(
        "crm.core.connection.socket.create_connection",
        lambda *a, **k: _DummySock(),
    )


def _register(m, *, whoami_status=200):
    m.get(_API_BASE, status_code=200, json={})  # TLS GET
    m.get(f"{_API_BASE}RetrieveVersion()", json=_VERSION)
    if whoami_status == 200:
        m.get(f"{_API_BASE}WhoAmI", json=_WHOAMI)
    else:
        m.get(f"{_API_BASE}WhoAmI", status_code=whoami_status,
              json={"error": {"code": "0x0", "message": "Unauthorized"}})


def test_doctor_json_happy_path():
    # AC: `crm --json connection doctor` → exit 0, ok true, all five checks.
    with requests_mock.Mocker() as m:
        _register(m)
        result = CliRunner().invoke(cli, ["--json", "connection", "doctor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    checks = payload["data"]["checks"]
    assert len(checks) == 5
    assert [c["check"] for c in checks] == [
        "dns_tcp", "tls", "version", "auth", "rate_limit",
    ]


def test_doctor_alias_matches_group_command():
    # AC: `crm --json doctor` behaves identically to `connection doctor` —
    # proves the top-level alias is wired to the same command object.
    with requests_mock.Mocker() as m:
        _register(m)
        result = CliRunner().invoke(cli, ["--json", "doctor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["data"]["checks"]) == 5


def test_doctor_json_auth_failure():
    # AC: a failing probe (WhoAmI 401) under --json → nonzero exit, ok false,
    # the auth check ok false.
    with requests_mock.Mocker() as m:
        _register(m, whoami_status=401)
        result = CliRunner().invoke(cli, ["--json", "connection", "doctor"])
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    auth = next(c for c in payload["data"]["checks"] if c["check"] == "auth")
    assert auth["ok"] is False


def test_doctor_human_failure_renders_checklist():
    # AC: human mode on a failure → nonzero exit, and the rendered checklist
    # is present (not just the bare error line emit would print).
    with requests_mock.Mocker() as m:
        _register(m, whoami_status=401)
        result = CliRunner().invoke(cli, ["connection", "doctor"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "Connection doctor" in combined
    assert "auth" in combined
    assert "dns_tcp" in combined
