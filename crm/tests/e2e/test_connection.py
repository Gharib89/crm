# pyright: basic
"""E2E tests for connection commands."""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers


@covers("connection whoami")
def test_whoami_returns_identity(backend, cli):
    # Direct backend: GUIDs present.
    result = backend.get("WhoAmI")
    assert result is not None
    assert "UserId" in result
    assert len(result["UserId"]) >= 36  # GUID-ish

    # CLI --json: enriched data shape and connection identity on meta (#624).
    import json as _json
    proc = cli(["--json", "connection", "whoami"])
    assert proc.returncode == 0
    env = _json.loads(proc.stdout)
    assert env["ok"] is True
    data = env["data"]
    # Original GUIDs still present.
    assert "UserId" in data
    assert "OrganizationId" in data
    # Enriched fields from whoami_identity (#624).
    assert "profile" in data
    assert "url" in data
    assert data["url"].startswith("http")
    assert "org_name" in data  # may be None on read failure, but key must exist
    # Connection identity on the success envelope.
    meta = env.get("meta", {})
    assert meta.get("profile") == data["profile"]
    assert meta.get("url") == data["url"]


@covers("connection status")
def test_connection_status_json(cli, tmp_path):
    env = {"CRM_HOME": str(tmp_path / ".d365")}
    result = cli(["--json", "connection", "status"], env=env)
    assert result.returncode == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["session"] == "default"
