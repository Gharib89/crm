# pyright: basic
"""E2E tests for connection commands."""

from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


@covers("connection whoami")
def test_whoami_returns_identity(backend, cli):
    """The CLI `--json` output carries the enriched data shape from whoami_identity and
    the connection identity on meta (#624). `org_name` may be None on read failure, but
    its key must exist.
    """
    result = backend.get("WhoAmI")
    assert result is not None
    assert "UserId" in result
    assert len(result["UserId"]) >= 36

    proc = cli(["--json", "connection", "whoami"])
    assert proc.returncode == 0
    env = json.loads(proc.stdout)
    assert env["ok"] is True
    data = env["data"]
    assert "UserId" in data
    assert "OrganizationId" in data
    assert "profile" in data
    assert "url" in data
    assert data["url"].startswith("http")
    assert "org_name" in data
    meta = env.get("meta", {})
    assert meta.get("profile") == data["profile"]
    assert meta.get("url") == data["url"]


@covers("connection whoami")
@pytest.mark.requires_onprem
def test_adfs_profile_signs_in_and_reads_the_version(backend, cli):
    """AD FS (IFD) sign-in end to end (#978): WS-Trust, MSISAuth cookies, Web API."""
    if backend.profile.auth_scheme != "adfs":
        pytest.skip("needs D365_E2E_PROFILE naming an adfs (IFD) profile")
    assert backend.get("WhoAmI")["UserId"]
    assert backend.get("RetrieveVersion")["Version"].startswith("9.")
    proc = cli(["--json", "connection", "whoami"])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True


@covers("connection status")
def test_connection_status_json(cli, tmp_path):
    env = {"CRM_HOME": str(tmp_path / ".d365")}
    result = cli(["--json", "connection", "status"], env=env)
    assert result.returncode == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["session"] == "default"
