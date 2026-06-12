# pyright: basic
"""E2E tests for entity CRUD (contact create/get/update/delete)."""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers


def _safe(backend, path: str) -> None:
    try:
        backend.delete(path)
    except Exception:
        pass


@covers("entity create", "entity get", "entity update", "entity delete")
def test_contact_crud_roundtrip(backend, request, unique):
    created = backend.post(
        "contacts",
        json_body={"firstname": "CLI", "lastname": f"Test-{unique}"},
        extra_headers={"If-None-Match": "null", "Prefer": "return=representation"},
    )
    cid = created["contactid"]
    request.addfinalizer(lambda: _safe(backend, f"contacts({cid})"))
    got = backend.get(f"contacts({cid})", params={"$select": "firstname"})
    assert got["firstname"] == "CLI"
    backend.patch(f"contacts({cid})", json_body={"telephone1": "+1-555-0001"},
                  extra_headers={"If-Match": "*"})
    assert backend.get(f"contacts({cid})", params={"$select": "telephone1"})["telephone1"] == "+1-555-0001"
    backend.delete(f"contacts({cid})")


@covers("entity create", "entity get", "entity delete")
def test_full_contact_workflow_cli(cli, tmp_path, unique):
    # Create
    body_path = tmp_path / "body.json"
    body_path.write_text(
        json.dumps({"firstname": "CLISub", "lastname": f"Test-{unique}"}),
        encoding="utf-8",
    )
    create = cli([
        "--json", "entity", "create", "contacts",
        "--data-file", str(body_path),
    ])
    assert create.returncode == 0, create.stderr
    env = json.loads(create.stdout)
    assert env["ok"], env
    contact_id = env["data"].get("contactid")
    assert contact_id

    try:
        # Get
        got = cli([
            "--json", "entity", "get", "contacts", contact_id,
            "--select", "fullname,firstname",
        ])
        assert got.returncode == 0, got.stderr
        assert json.loads(got.stdout)["data"]["firstname"] == "CLISub"
    finally:
        # Delete (idempotent finalizer)
        cli([
            "--json", "entity", "delete", "contacts", contact_id, "--yes",
        ], check=False)
