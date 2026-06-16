# pyright: basic
"""E2E for upsert by alternate key (#335) — `entity upsert --key` and
`data import --mode upsert --key`.

Self-provisioning: creates a custom entity whose primary attribute backs an
alternate key, waits for the key's index to activate (asynchronous in
Dataverse), exercises both verbs by that key, then deletes the entity. Skips
gracefully if the index does not activate within the timeout.
"""
from __future__ import annotations

import json
import time

import pytest

from crm.tests.e2e.coverage import covers

_KEY_LABEL = {
    "@odata.type": "Microsoft.Dynamics.CRM.Label",
    "LocalizedLabels": [{
        "@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel",
        "Label": "Code Key", "LanguageCode": 1033,
    }],
}


def _wait_for_active_key(backend, logical: str, *, timeout: int = 180) -> bool:
    from crm.core import metadata as meta_mod
    deadline = time.time() + timeout
    while time.time() < deadline:
        keys = meta_mod.list_entity_keys(backend, logical)
        if keys and keys[0]["index_status"] == "Active":
            return True
        time.sleep(5)
    return False


@covers("entity upsert", "data import")
@pytest.mark.slow
def test_upsert_by_alternate_key(backend, cli, unique, tmp_path):
    from crm.core import metadata as meta_mod

    schema = f"new_E2EAK{unique}"
    logical = schema.lower()
    key_schema = f"new_Code{unique}"
    key_attr = key_schema.lower()  # primary attribute = the alternate-key column

    meta_mod.create_entity(
        backend, schema_name=schema, display_name=f"E2E AK {unique}",
        primary_attr_schema=key_schema, primary_attr_label="Code",
    )
    try:
        # Resolve the OData entity-set name (Dataverse pluralizes the logical name).
        defn = backend.get(
            f"EntityDefinitions(LogicalName='{logical}')",
            params={"$select": "EntitySetName"},
        )
        entity_set = defn["EntitySetName"]

        # Define the alternate key on the primary attribute, then wait for its
        # asynchronous index to activate.
        backend.post(
            f"EntityDefinitions(LogicalName='{logical}')/Keys",
            json_body={
                "@odata.type": "Microsoft.Dynamics.CRM.EntityKeyMetadata",
                "SchemaName": f"new_codekey{unique}",
                "KeyAttributes": [key_attr],
                "DisplayName": _KEY_LABEL,
            },
        )
        if not _wait_for_active_key(backend, logical):
            pytest.skip("alternate-key index did not activate within the timeout")

        # entity upsert --key: first call creates, second matches the same record.
        create = cli(["--json", "entity", "upsert", entity_set,
                      "--key", key_attr,
                      "--data", json.dumps({key_attr: "ALPHA-001"})])
        assert create.returncode == 0, create.stderr
        assert json.loads(create.stdout)["ok"]

        update = cli(["--json", "entity", "upsert", entity_set,
                      "--key", key_attr,
                      "--data", json.dumps({key_attr: "ALPHA-001"})])
        assert update.returncode == 0, update.stderr
        assert json.loads(update.stdout)["ok"]

        # data import --mode upsert --key: one existing key (match) + one new (create).
        rows_file = tmp_path / "rows.jsonl"
        rows_file.write_text(
            "\n".join([
                json.dumps({key_attr: "ALPHA-001"}),
                json.dumps({key_attr: "BETA-002"}),
            ]) + "\n",
            encoding="utf-8",
        )
        rows = cli(["--json", "data", "import", entity_set, str(rows_file),
                    "--mode", "upsert", "--key", key_attr])
        assert rows.returncode == 0, rows.stderr
        imported = json.loads(rows.stdout)["data"]["imported"]
        assert imported == 2, rows.stdout

        # Exactly two rows exist — the alternate key deduped ALPHA-001.
        listing = cli(["--json", "query", "odata", entity_set, "--select", key_attr])
        assert listing.returncode == 0, listing.stderr
        value = json.loads(listing.stdout)["data"]
        value = value["value"] if isinstance(value, dict) else value
        codes = sorted(r.get(key_attr) for r in value)
        assert codes == ["ALPHA-001", "BETA-002"], codes
    finally:
        try:
            meta_mod.delete_entity(backend, logical)
        except Exception:
            pass
