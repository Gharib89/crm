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
