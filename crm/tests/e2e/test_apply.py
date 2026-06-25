# pyright: basic
"""E2E tests for the apply command (declarative desired-state spec)."""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


@pytest.mark.slow
@covers("apply")
def test_apply_add_attribute_to_ephemeral_entity(cli, backend, ephemeral_entity, tmp_path, request):
    """Apply a spec that adds a single string attribute to the ephemeral entity.

    Adding one attribute to an existing entity is a minimal reversible change:
    - No publisher/solution creation is needed (spec can omit them).
    - The attribute is cleaned up in a finalizer so re-runs are safe.
    - Exercises a distinct backend path (PublishAllXml at the end of apply).
    """
    suffix = ephemeral_entity[-8:]
    attr_schema = f"new_applytest_{suffix}"

    spec = {
        "entities": [
            {
                "schema_name": ephemeral_entity,
                "display_name": f"E2E {suffix}",
                "attributes": [
                    {
                        "kind": "string",
                        "schema_name": attr_schema,
                        "display_name": f"Apply Test {suffix}",
                        "max_length": 50,
                    }
                ],
            }
        ]
    }
    spec_path = tmp_path / "apply_spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def _cleanup():
        try:
            backend.delete(
                f"EntityDefinitions(LogicalName='{ephemeral_entity}')"
                f"/Attributes(LogicalName='{attr_schema.lower()}')"
            )
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    result = cli(["--json", "apply", "-f", str(spec_path)])
    data = json.loads(result.stdout)
    assert data["ok"] is True, f"apply failed: {data}"
    applied = data["data"].get("applied", [])
    failed = data["data"].get("failed", [])
    assert not failed, f"apply reported failures: {failed}"
    # The attribute was either created (applied) or already exists (skipped on re-run)
    names = {e.get("name") for e in applied}
    assert attr_schema in names or len(data["data"].get("skipped", [])) > 0, (
        f"attribute {attr_schema!r} neither applied nor skipped: {data['data']}"
    )


@pytest.mark.slow
@covers("apply")
def test_apply_reconciles_existing_attribute(cli, backend, ephemeral_entity, tmp_path, request):
    """Convergent apply: re-applying a changed spec UPDATES an existing attribute
    in place, an unchanged re-apply is a no-op, and a data-type change is
    REPLACE-BLOCKED (reported, no write, ok=false, exit 1).

    All three behaviors are target-agnostic — display/max-length updates and the
    refuse-on-retype guard work on both on-prem and cloud — so this runs on either
    live target. The attribute is cleaned up in a finalizer.
    """
    from crm.core import metadata as meta_mod

    suffix = ephemeral_entity[-8:]
    attr_schema = f"new_applyconv_{suffix}"
    attr_logical = attr_schema.lower()

    def _cleanup():
        try:
            backend.delete(
                f"EntityDefinitions(LogicalName='{ephemeral_entity}')"
                f"/Attributes(LogicalName='{attr_logical}')"
            )
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    def _spec_path(attr):
        spec = {"entities": [{"schema_name": ephemeral_entity,
                              "display_name": f"E2E {suffix}", "attributes": [attr]}]}
        path = tmp_path / "conv_spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return str(path)

    base_attr = {"kind": "string", "schema_name": attr_schema,
                 "display_name": "Conv Test", "max_length": 50}

    # 1) Seed the column (applied first run, skipped on a re-run — either is fine).
    seed = json.loads(cli(["--json", "apply", "-f", _spec_path(base_attr)]).stdout)
    assert seed["ok"] is True, f"seed apply failed: {seed}"

    # 2) Update path: rename + grow max-length → reported `updated`, no longer skipped.
    grown = {**base_attr, "display_name": "Conv Test Renamed", "max_length": 120}
    upd = json.loads(cli(["--json", "apply", "-f", _spec_path(grown)]).stdout)
    assert upd["ok"] is True, f"update apply failed: {upd}"
    assert any(e.get("name") == attr_schema for e in upd["data"]["updated"]), (
        f"attribute not in updated bucket: {upd['data']}")

    # 3) Idempotent: re-applying the now-matching spec updates nothing.
    again = json.loads(cli(["--json", "apply", "-f", _spec_path(grown)]).stdout)
    assert again["ok"] is True, f"idempotent re-apply failed: {again}"
    assert again["data"]["updated"] == [], f"expected no-op, got: {again['data']}"

    # 4) Replace-blocked: declaring the same column as a different data type is a
    #    destructive divergence → ok=false, reported, NO write.
    retyped = {"kind": "integer", "schema_name": attr_schema,
               "display_name": "Conv Test Renamed"}
    blocked = json.loads(cli(["--json", "apply", "-f", _spec_path(retyped)], check=False).stdout)
    assert blocked["ok"] is False, f"expected replace-blocked failure, got: {blocked}"
    assert any(e.get("name") == attr_schema for e in blocked["data"]["replace_blocked"]), (
        f"attribute not in replace_blocked bucket: {blocked['data']}")
    # The live column is untouched — still a string, not retyped.
    live = meta_mod.attribute_info(backend, ephemeral_entity, attr_logical)
    assert "String" in str(live.get("AttributeType")), f"column was retyped: {live}"


@pytest.mark.slow
@covers("apply")
def test_apply_dry_run_drift_report_writes_nothing(cli, backend, ephemeral_entity,
                                                    tmp_path, request):
    """--dry-run reads the live org and reports drift WITHOUT writing (#550).

    Seed a column, then dry-run a spec that drifts it: the column must land in the
    `updated` drift bucket, meta.dry_run is set, and the live column is left
    byte-for-byte unchanged — proving the reads-execute rule (GETs run, writes
    suppressed). Target-agnostic; runs on either live target. Column cleaned up in
    a finalizer.
    """
    from crm.core import metadata as meta_mod

    suffix = ephemeral_entity[-8:]
    attr_schema = f"new_applydry_{suffix}"
    attr_logical = attr_schema.lower()

    def _cleanup():
        try:
            backend.delete(
                f"EntityDefinitions(LogicalName='{ephemeral_entity}')"
                f"/Attributes(LogicalName='{attr_logical}')"
            )
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    def _spec_path(attr):
        spec = {"entities": [{"schema_name": ephemeral_entity,
                              "display_name": f"E2E {suffix}", "attributes": [attr]}]}
        path = tmp_path / "dry_spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return str(path)

    base_attr = {"kind": "string", "schema_name": attr_schema,
                 "display_name": "Dry Test", "max_length": 50}

    # Seed the column for real (applied first run, skipped on re-run — both fine).
    seed = json.loads(cli(["--json", "apply", "-f", _spec_path(base_attr)]).stdout)
    assert seed["ok"] is True, f"seed apply failed: {seed}"

    # Dry-run a drifted spec: grow max-length + rename → reported as `updated`
    # drift, but NO write is issued.
    drifted = {**base_attr, "display_name": "Dry Test Renamed", "max_length": 120}
    preview = json.loads(
        cli(["--json", "--dry-run", "apply", "-f", _spec_path(drifted)]).stdout)
    assert preview["ok"] is True, f"dry-run apply failed: {preview}"
    assert preview["meta"]["dry_run"] is True, f"dry_run flag missing: {preview}"
    assert preview["meta"]["staged"] is False, f"dry-run should not stage: {preview}"
    assert any(e.get("name") == attr_schema for e in preview["data"]["updated"]), (
        f"drifted column not in updated bucket: {preview['data']}")

    # The live column is unchanged — the dry-run wrote nothing.
    live = meta_mod.attribute_info(backend, ephemeral_entity, attr_logical)
    assert meta_mod.label_text(live.get("DisplayName") or {}) == "Dry Test", (
        f"dry-run mutated the display name: {live.get('DisplayName')}")


@pytest.mark.slow
@covers("apply")
def test_apply_webresource_create_noop_update(cli, backend, tmp_path, unique, request):
    """apply creates a web resource from a file, no-ops an unchanged re-apply, and
    updates its content when the file changes (convergent). Target-agnostic — web
    resource CRUD + PublishAllXml work on both targets. Cleaned up in a finalizer.
    """
    name = f"new_e2e_apply_{unique}.js"
    js = tmp_path / "app.js"
    js.write_bytes(b"// e2e apply v1\n")
    spec = {"webresources": [{"name": name, "file": str(js),
                              "display_name": f"E2E apply WR {unique}"}]}
    spec_path = tmp_path / "wr_spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def _cleanup():
        try:
            rows = backend.get_collection("webresourceset", params={
                "$filter": f"name eq '{name}'", "$select": "webresourceid"})
            if rows:
                backend.delete(f"webresourceset({rows[0]['webresourceid']})")
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    # CREATE
    created = json.loads(cli(["--json", "apply", "-f", str(spec_path)]).stdout)
    assert created["ok"] is True, f"create apply failed: {created}"
    assert any(e.get("kind") == "webresource" and e.get("name") == name
               for e in created["data"]["applied"]), f"WR not applied: {created['data']}"

    # NO-OP: identical content re-applies to a skip (convergent idempotence).
    noop = json.loads(cli(["--json", "apply", "-f", str(spec_path)]).stdout)
    assert noop["ok"] is True, f"no-op apply failed: {noop}"
    assert any(e.get("name") == name for e in noop["data"]["skipped"]), (
        f"unchanged WR not skipped: {noop['data']}")
    assert noop["data"]["updated"] == [], f"expected no update: {noop['data']}"

    # CONTENT DRIFT → update.
    js.write_bytes(b"// e2e apply v2 CHANGED\n")
    upd = json.loads(cli(["--json", "apply", "-f", str(spec_path)]).stdout)
    assert upd["ok"] is True, f"update apply failed: {upd}"
    assert any(e.get("kind") == "webresource" and e.get("name") == name
               for e in upd["data"]["updated"]), f"WR not updated on drift: {upd['data']}"


@pytest.mark.slow
@covers("apply")
def test_apply_security_role_create_and_reconcile(cli, backend, tmp_path, unique, request):
    """apply creates a security role and applies the declared privileges, then an
    unchanged re-apply is a convergent no-op. (The role also keeps Dataverse's
    immovable baseline privileges, so this asserts the declared set is satisfied,
    not strict equality.) Target-agnostic — role create + ReplacePrivilegesRole work
    on both targets (on-prem is the issue's priority). Role deleted in a finalizer.
    """
    role_name = f"E2E Apply Role {unique}"
    spec = {"security_roles": [{
        "name": role_name,
        "privileges": [{"privilege_names": ["prvReadAccount"], "depth": "global"}],
    }]}
    spec_path = tmp_path / "role_spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def _cleanup():
        try:
            rows = backend.get_collection("roles", params={
                "$filter": f"name eq '{role_name}'", "$select": "roleid"})
            for r in rows:
                backend.delete(f"roles({r['roleid']})")
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    # CREATE role + set declared privileges.
    created = json.loads(cli(["--json", "apply", "-f", str(spec_path)]).stdout)
    assert created["ok"] is True, f"create apply failed: {created}"
    assert any(e.get("kind") == "security-role" and e.get("name") == role_name
               for e in created["data"]["applied"]), f"role not applied: {created['data']}"

    # NO-OP: privileges already exactly the declared set → convergent skip.
    noop = json.loads(cli(["--json", "apply", "-f", str(spec_path)]).stdout)
    assert noop["ok"] is True, f"no-op apply failed: {noop}"
    assert any(e.get("name") == role_name for e in noop["data"]["skipped"]), (
        f"unchanged role not skipped: {noop['data']}")
    assert noop["data"]["updated"] == [], f"expected no update: {noop['data']}"


@pytest.mark.slow
@pytest.mark.requires_onprem
@covers("apply")
def test_apply_plugin_assembly_type_step_lifecycle(
        cli, plugin_assembly, backend, tmp_path, request):
    """apply provisions a plug-in from a spec — assembly + type + step + image in
    one run — then converges: an unchanged re-apply is a no-op, a step-config drift
    updates in place, and a step binding change (the message) is replace-blocked (no
    write, exit 1). On-prem is the plug-in extensibility target (#552), and is
    pinned here deliberately: on-prem metadata writes are synchronous, so the
    single-apply assembly→type→step→image sequence resolves each just-created row
    immediately. Dataverse (cloud) has a metadata read-after-write lag (the
    assembly-lifecycle test polls up to 20s for a new plug-in type to become
    queryable), which a single-shot apply cannot poll around — so the weekly cloud
    e2e run gates this out rather than flaking. The `plugin_assembly` fixture builds
    a signed no-op IPlugin so registration is proven without ever firing it (and
    skips with instructions when dotnet is absent). The assembly is unregistered in
    a finalizer (cascading its type, step, and images).
    """
    asm = plugin_assembly
    step_name = f"{asm.assembly_name} apply step"

    def _spec(*, rank, message):
        spec = {"plugins": [{
            "assembly": asm.assembly_name,
            "file": asm.dll,
            "public_key_token": asm.public_key_token,
            "version": "1.0.0.0",
            "isolation_mode": "sandbox",
            "types": [{"type_name": asm.type_name}],
            "steps": [{
                "name": step_name,
                "message": message,
                "entity": "account",
                "plugin_type": asm.type_name,
                "stage": "postoperation",
                "rank": rank,
                # A post-image is valid on a Create step in PostOperation, so the
                # image declare/register/skip path is covered live too.
                "images": [{"alias": "PostImage", "image_type": "post",
                            "attributes": "name"}],
            }],
        }]}
        path = tmp_path / "plugin_spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return str(path)

    def _cleanup():
        # Unregister the assembly by name (cascades its type, step, images). Runs
        # even if a mid-test apply left only the assembly registered.
        try:
            rows = backend.get_collection(
                "pluginassemblies",
                params={"$filter": f"name eq '{asm.assembly_name}'",
                        "$select": "pluginassemblyid"})
            for r in rows:
                cli(["--json", "plugin", "unregister-assembly",
                     r["pluginassemblyid"], "--yes"], check=False)
        except Exception:
            pass
    request.addfinalizer(_cleanup)

    # 1. CREATE: assembly + type + step provisioned in a single apply.
    created = json.loads(cli(["--json", "apply", "-f", _spec(rank=1, message="Create")]).stdout)
    assert created["ok"] is True, f"create apply failed: {created}"
    applied = {(e["kind"], e["name"]) for e in created["data"]["applied"]}
    assert ("plugin-assembly", asm.assembly_name) in applied, created["data"]
    assert ("plugin-type", asm.type_name) in applied, created["data"]
    assert ("plugin-step", step_name) in applied, created["data"]
    assert ("plugin-image", "PostImage") in applied, created["data"]

    # 2. NO-OP: re-applying the unchanged spec converges to skips, no update.
    noop = json.loads(cli(["--json", "apply", "-f", _spec(rank=1, message="Create")]).stdout)
    assert noop["ok"] is True, f"no-op apply failed: {noop}"
    assert noop["data"]["updated"] == [], f"expected no update: {noop['data']}"
    assert ("plugin-step", step_name) in {
        (e["kind"], e["name"]) for e in noop["data"]["skipped"]}, noop["data"]

    # 3. UPDATE: drift the step's rank → in-place config update.
    upd = json.loads(cli(["--json", "apply", "-f", _spec(rank=7, message="Create")]).stdout)
    assert upd["ok"] is True, f"update apply failed: {upd}"
    assert any(e["kind"] == "plugin-step" and e["name"] == step_name
               for e in upd["data"]["updated"]), f"step not updated: {upd['data']}"

    # 4. REPLACE-BLOCKED: changing the step's message is a binding change needing a
    # destructive delete-and-recreate → reported, no write for it, exit 1.
    blocked = json.loads(
        cli(["--json", "apply", "-f", _spec(rank=7, message="Update")], check=False).stdout)
    assert blocked["ok"] is False, f"expected replace-blocked: {blocked}"
    assert any(e["kind"] == "plugin-step" and e["name"] == step_name
               for e in blocked["data"]["replace_blocked"]), blocked["data"]
