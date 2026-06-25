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
