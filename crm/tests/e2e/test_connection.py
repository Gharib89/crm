# pyright: basic
"""E2E tests for connection commands."""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers


@covers("connection whoami")
def test_whoami_returns_identity(backend):
    result = backend.get("WhoAmI")
    assert result is not None
    assert "UserId" in result
    assert len(result["UserId"]) >= 36  # GUID-ish


@covers("connection status")
def test_connection_status_json(cli, tmp_path):
    env = {"CRM_HOME": str(tmp_path / ".d365")}
    result = cli(["--json", "connection", "status"], env=env)
    assert result.returncode == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["session"] == "default"
