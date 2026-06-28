# pyright: basic
"""E2E test for `crm solution export-spec` (#613, #614).

Adds a custom entity, a security role, and a web resource to a throwaway
unmanaged solution, then projects the whole solution into a merged
apply-consumable spec and asserts all three kinds round-trip into the spec
(entities, security_roles, webresources). Pure-GET projection plus the setup
writes — runs on both the on-prem (NTLM) and Dataverse-online targets.
"""
from __future__ import annotations

import json

import yaml

from crm.tests.e2e.coverage import covers


@covers("solution export-spec")
def test_export_spec_projects_entity_role_and_webresource(
    cli, backend, ephemeral_solution, ephemeral_entity, unique, tmp_path
):
    from crm.core import apply as apply_mod
    from crm.core import metadata as meta_mod
    from crm.core import security as sec_mod
    from crm.core import solution as sol_mod
    from crm.core import webresource as wr_mod

    # Entity (componenttype 1) → add the session entity to this solution.
    info = meta_mod.entity_info(backend, ephemeral_entity)
    sol_mod.add_solution_component(
        backend, solution=ephemeral_solution,
        component_id=info["MetadataId"], component_type=1,
    )

    role_name = f"new_e2e_role_{unique}"
    wr_name = f"new_e2e_{unique}.js"
    role_id = None
    try:
        # Security role (20) → created into the solution, then granted one
        # authorable privilege so it projects (a privilege-less role can't
        # round-trip — build_role_spec would route it to skipped).
        role = sec_mod.create_role(backend, role_name, solution=ephemeral_solution)
        role_id = role["roleid"]
        sec_mod.set_role_privileges(
            backend, role_id, access=["read"], entities=["account"], depth="Basic")
        # Web resource (61) → created into the solution with real JS content.
        wr_mod.create_webresource(
            backend, name=wr_name, content=b"// e2e export-spec\n",
            webresourcetype=3, solution=ephemeral_solution)

        # 1) Summary envelope (no -o): all three kinds project; skipped is a list.
        result = cli(["--json", "solution", "export-spec", ephemeral_solution])
        assert result.returncode == 0, result.stderr
        env = json.loads(result.stdout)
        assert env["ok"], env
        data = env["data"]
        assert isinstance(data["skipped"], list)
        assert any(ephemeral_entity.lower() == str(s).lower() for s in data["entities"]), (
            f"expected {ephemeral_entity!r} among entities, got {data['entities']}")
        assert role_name in data["security_roles"], data["security_roles"]
        assert wr_name in data["webresources"], data["webresources"]

        # 2) Bare YAML (-o): apply-ready, self-contained spec.
        out = tmp_path / "spec.yaml"
        r2 = cli(["--json", "solution", "export-spec", ephemeral_solution, "-o", str(out)])
        assert r2.returncode == 0, r2.stderr
        written = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert written["solution"] == {"unique_name": ephemeral_solution}
        assert "skipped" not in written

        roles = {r["name"]: r for r in written.get("security_roles", [])}
        assert role_name in roles, roles
        assert roles[role_name]["privileges"], "role projected with no privilege rows"

        wrs = {w["name"]: w for w in written.get("webresources", [])}
        assert wr_name in wrs, wrs
        assert wrs[wr_name]["content"], "web resource projected with no inline content"
        assert wrs[wr_name]["webresourcetype"] == 3

        # Load-bearing: the merged spec (now with roles + web resources) validates.
        apply_mod.validate_spec(written)
    finally:
        if role_id:
            try:
                backend.delete(f"roles({role_id})")
            except Exception:  # noqa: BLE001 — best-effort cleanup, never mask the test
                pass
        try:
            wr_mod.delete_webresource(backend, wr_name)
        except Exception:  # noqa: BLE001
            pass
