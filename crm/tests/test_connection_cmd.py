"""Command-level tests for `crm connection connect` api_version negotiation (#51)."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli

_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001"}


def _profile_json(home, name):
    return json.loads((home / ".crm" / "profiles" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    # Snapshot/restore os.environ around each test: connect/test →
    # resolve_credentials → load_dotenv() writes .env values straight into
    # os.environ (monkeypatch can't undo those), which would leak into later
    # tests (cf. #56). Default CRM_DOTENV to a noop path so the repo's real
    # ./.env is never autoloaded; tests may override it.
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestConnectCommandNegotiation:
    def test_connect_onprem_persists_v91(self, tmp_path):
        # AC: connect against an on-prem org without --api-version yields a
        # working profile whose saved api_version is v9.1.
        base = "https://internalcrm.moce.local/MOCE"
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            m.get(f"{base}/api/data/v9.1/WhoAmI", json=_WHOAMI)
            result = CliRunner().invoke(cli, [
                "connection", "connect", "--url", base,
                "--username", "alice", "--domain", "MOCE",
                "--password", "pw", "--profile-name", "onprem",
            ])
        assert result.exit_code == 0, result.output
        assert _profile_json(tmp_path, "onprem")["api_version"] == "v9.1"

    def test_connect_cloud_keeps_v92(self, tmp_path):
        # AC: cloud/OAuth-style org keeps v9.2, no needless downgrade probe.
        base = "https://contoso.crm.dynamics.com"
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            result = CliRunner().invoke(cli, [
                "connection", "connect", "--url", base,
                "--username", "alice", "--password", "pw",
                "--profile-name", "cloud",
            ])
            assert all("/v9.1/" not in r.url for r in m.request_history)
        assert result.exit_code == 0, result.output
        assert _profile_json(tmp_path, "cloud")["api_version"] == "v9.2"

    def test_connect_explicit_version_not_downgraded(self):
        # AC: an explicitly-requested version 501s through, never auto-downgraded.
        base = "https://internalcrm.moce.local/MOCE"
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            result = CliRunner().invoke(cli, [
                "connection", "connect", "--url", base,
                "--username", "alice", "--password", "pw",
                "--profile-name", "pinned", "--api-version", "v9.2",
            ])
            assert all("/v9.1/" not in r.url for r in m.request_history)
        assert result.exit_code != 0  # operational failure surfaced

    def test_test_respects_version_pinned_in_dotenv(self, tmp_path):
        # A version pinned only in .env must be honoured: `connection test`
        # must NOT negotiate/downgrade it, even on a 501. Regression for the
        # decide-before-dotenv-autoload bug (PR #57 review). The autouse
        # snapshot/restore fixture undoes the load_dotenv() env writes.
        base = "https://internalcrm.moce.local/MOCE"
        env_file = tmp_path / "pinned.env"
        env_file.write_text(
            "\n".join([
                f"D365_URL={base}",
                "D365_USERNAME=alice",
                "D365_PASSWORD=pw",
                "D365_DOMAIN=MOCE",
                "D365_AUTH=ntlm",
                "D365_API_VERSION=v9.2",
            ]) + "\n",
            encoding="utf-8",
        )
        os.environ["CRM_DOTENV"] = str(env_file)  # override autouse noop
        for k in ("D365_API_VERSION", "CRM_API_VERSION"):
            os.environ.pop(k, None)  # ensure only the .env supplies it
        with requests_mock.Mocker() as m:
            m.get(f"{base}/api/data/v9.2/WhoAmI", status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            result = CliRunner().invoke(cli, ["connection", "test"])
            assert all("/v9.1/" not in r.url for r in m.request_history)
        assert result.exit_code != 0  # 501 surfaces, no silent downgrade
