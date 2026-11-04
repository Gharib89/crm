# pyright: basic
"""E2E tests for `crm legacy` against a live CRM 2011 org (read-only).

Run with ``D365_E2E=1 D365_E2E_LEGACY_PROFILE=<crm2011 profile>``; see TEST.md.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from crm.tests.e2e.coverage import covers

pytestmark = pytest.mark.requires_legacy

# Captured at import, before the v9 live_profile fixture can repoint CRM_HOME at
# its throwaway home: the crm2011 profile lives in the developer's real home.
_REAL_HOME = os.environ.get("CRM_HOME", "")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _legacy(cli, verb: str) -> dict:
    name = os.environ["D365_E2E_LEGACY_PROFILE"].strip()
    proc = cli(
        ["--json", "--profile", name, "legacy", verb], check=False, env={"CRM_HOME": _REAL_HOME}
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    env = json.loads(proc.stdout)
    assert env["ok"] is True
    assert env["meta"]["profile"] == name
    assert env["meta"]["url"].endswith("/XRMServices/2011/Organization.svc/web")
    return env["data"]


@covers("legacy version")
def test_legacy_version_is_a_crm2011_build(cli):
    assert _legacy(cli, "version")["version"].startswith("5.")


@covers("legacy whoami")
def test_legacy_whoami_returns_the_three_ids(cli):
    data = _legacy(cli, "whoami")
    for key in ("user_id", "business_unit_id", "organization_id"):
        assert _GUID.match(data[key]), key
