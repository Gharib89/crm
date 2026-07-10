# pyright: basic
"""E2E test for `crm org brief` — the one-call org inventory (#790).

Read-only: asserts the brief runs green against a live org and carries every
section with its documented shape. Values are org-dependent, so the test asserts
structure and self-identification, not specific counts.
"""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers


@covers("org brief")
def test_org_brief(cli):
    r = cli(["--json", "org", "brief"])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"]
    data = env["data"]

    # Every section present.
    for section in ("identity", "solutions", "publishers", "schema", "apps",
                    "automation", "components"):
        assert section in data, f"missing section {section!r}: {data}"

    # Identity is self-describing: a real org name/version and the WhoAmI ids.
    ident = data["identity"]
    assert ident["version"], "org version should be a non-empty build string"
    assert ident["organization_id"]
    assert ident["user_id"]

    # Solutions: every org has the Default + Active system solutions (unmanaged),
    # which are excluded from the candidate --solution target list.
    sol = data["solutions"]
    assert sol["unmanaged"] >= 2
    assert "Default" not in sol["unmanaged_names"]
    assert "Active" not in sol["unmanaged_names"]

    # Publishers: every org has at least the default publisher, with a prefix.
    pubs = data["publishers"]["items"]
    assert data["publishers"]["count"] >= 1
    assert all("prefix" in p for p in pubs)

    # Schema + apps + automation + components: shape only (counts are org-specific).
    schema = data["schema"]
    assert isinstance(schema["custom_entities"], int)
    assert schema["global_optionsets"] >= 1  # every org ships global option sets
    assert isinstance(data["apps"]["count"], int)

    auto = data["automation"]
    assert isinstance(auto["plugin_assemblies"], int)
    assert isinstance(auto["plugin_steps"], int)
    assert isinstance(auto["workflows"]["by_category"], dict)
    assert isinstance(auto["slas"], int)

    comp = data["components"]
    assert isinstance(comp["webresources"]["total"], int)
    assert set(comp["webresources"]["by_type"]) == {"html", "css", "script"}
    assert isinstance(comp["security_roles_custom"], int)
    assert isinstance(comp["duplicate_rules"], int)

    # Self-identifying envelope (#624).
    assert env["meta"]["profile"]
    assert env["meta"]["url"]
