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
    # Normalized id contract (ADR 0008 / #303): create surfaces _entity_id
    # alongside the full record, and strips @odata.* protocol keys.
    assert env["data"]["_entity_id"] == contact_id
    assert env["data"]["_entity_id_url"].endswith(f"contacts({contact_id})")
    assert not any("@odata." in k for k in env["data"])

    try:
        # Get — full record carries _entity_id, @odata.* stripped.
        got = cli([
            "--json", "entity", "get", "contacts", contact_id,
            "--select", "fullname,firstname",
        ])
        assert got.returncode == 0, got.stderr
        got_data = json.loads(got.stdout)["data"]
        assert got_data["firstname"] == "CLISub"
        assert got_data["_entity_id"] == contact_id
        assert not any("@odata." in k for k in got_data)
    finally:
        # Delete — returns {deleted, _entity_id, _entity_id_url} (not bare `id`).
        deleted = cli([
            "--json", "entity", "delete", "contacts", contact_id, "--yes",
        ], check=False)
        if deleted.returncode == 0:
            ddata = json.loads(deleted.stdout)["data"]
            assert ddata["deleted"] is True
            assert ddata["_entity_id"] == contact_id
            assert "id" not in ddata


@covers("entity upsert")
def test_entity_upsert_if_none_match_is_create_only(backend, cli, unique):
    import uuid

    # Upsert to a fresh client-chosen GUID with --if-none-match: the record is
    # absent, so it creates. A second create-only upsert to the same id must fail
    # with a precondition error (If-None-Match: * → 412) instead of updating.
    cid = str(uuid.uuid4())
    created = False
    try:
        first = cli([
            "--json", "entity", "upsert", "contacts", cid,
            "--data", json.dumps({"firstname": "INM", "lastname": f"Test-{unique}"}),
            "--if-none-match",
        ])
        assert first.returncode == 0, first.stderr
        created = True

        second = cli([
            "--json", "entity", "upsert", "contacts", cid,
            "--data", json.dumps({"firstname": "INM2", "lastname": f"Test-{unique}"}),
            "--if-none-match",
        ], check=False)
        assert second.returncode != 0, "create-only upsert should fail when the record exists"
        env = json.loads(second.stdout)
        assert env["ok"] is False
    finally:
        if created:
            _safe(backend, f"contacts({cid})")
