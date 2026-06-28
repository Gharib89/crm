# pyright: basic
"""E2E tests for metadata WRITE verbs:
  - metadata update-entity
  - metadata update-attribute
  - metadata delete-attribute
  - metadata create-many-to-many  (+ metadata delete-relationship)
  - metadata update-relationship  (uses a 1:N created in the same lifecycle test)
  - metadata clone-entity

All tests use `ephemeral_entity` as the base so they never pay the
create+publish cost themselves. Each test cleans up exactly what it creates;
shared session state is left to the conftest session-scope fixture.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


# ---------------------------------------------------------------------------
# metadata update-entity
# ---------------------------------------------------------------------------

@covers("metadata update-entity")
@pytest.mark.slow
def test_update_entity_display_name(cli, ephemeral_entity):
    """Update the ephemeral entity's display name and assert the change is
    read back from the server."""
    new_display = f"E2E Updated {ephemeral_entity}"
    r = cli([
        "--json", "metadata", "update-entity", ephemeral_entity,
        "--display", new_display,
        "--no-publish",
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("updated") is True
    assert data.get("logical_name") == ephemeral_entity

    # Read back via the CLI and confirm the server sees the new label.
    r2 = cli(["--json", "metadata", "entity", ephemeral_entity])
    assert r2.returncode == 0, r2.stderr
    env2 = json.loads(r2.stdout)
    assert env2["ok"]
    # DisplayName is a LocalizedLabels object; check the UserLocalizedLabel text.
    dn = env2["data"].get("DisplayName") or {}
    ull = dn.get("UserLocalizedLabel") or {}
    assert ull.get("Label") == new_display, (
        f"Expected '{new_display}', got: {json.dumps(dn)}"
    )


@covers("metadata update-entity")
@pytest.mark.slow
def test_update_entity_description(cli, ephemeral_entity):
    """Update description; confirm updated=True (no redundant readback needed since
    test_update_entity_display_name already exercises the GET path)."""
    r = cli([
        "--json", "metadata", "update-entity", ephemeral_entity,
        "--description", "E2E description set by test_update_entity_description",
        "--no-publish",
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"], env
    assert env["data"].get("updated") is True


# ---------------------------------------------------------------------------
# metadata update-attribute  +  metadata delete-attribute  (shared lifecycle)
# ---------------------------------------------------------------------------

@covers("metadata update-attribute", "metadata delete-attribute")
@pytest.mark.slow
def test_update_and_delete_string_attribute(cli, ephemeral_entity, unique):
    """Full attribute lifecycle on ephemeral_entity:
      1. add-attribute (string) to get a test column
      2. update-attribute --display / --max-length; assert updated=True
      3. delete-attribute --yes; assert deleted=True
    Cleanup in finally ensures the attribute is gone even if assertions fail.
    """
    attr_schema = f"new_e2etest{unique}"
    attr_logical = attr_schema.lower()

    # Step 1: add the attribute so we have something to update/delete.
    r_add = cli([
        "--json", "metadata", "add-attribute", ephemeral_entity,
        "--kind", "string",
        "--schema-name", attr_schema,
        "--display", f"E2E Test {unique}",
        "--max-length", "150",
        "--no-publish",
    ])
    assert r_add.returncode == 0, r_add.stderr
    env_add = json.loads(r_add.stdout)
    assert env_add["ok"], env_add

    try:
        # Step 2: update-attribute — change display label and max-length.
        r_upd = cli([
            "--json", "metadata", "update-attribute", ephemeral_entity, attr_logical,
            "--display", f"E2E Updated {unique}",
            "--max-length", "200",
            "--no-publish",
        ])
        assert r_upd.returncode == 0, r_upd.stderr
        env_upd = json.loads(r_upd.stdout)
        assert env_upd["ok"], env_upd
        assert env_upd["data"].get("updated") is True

        # Verify the server reflects the new max-length.
        r_read = cli(["--json", "metadata", "attribute", ephemeral_entity, attr_logical])
        assert r_read.returncode == 0, r_read.stderr
        attr_data = json.loads(r_read.stdout)["data"]
        assert attr_data.get("MaxLength") == 200, (
            f"Expected MaxLength=200, got: {attr_data.get('MaxLength')}"
        )

        # Step 3: delete-attribute.
        r_del = cli([
            "--json", "metadata", "delete-attribute", ephemeral_entity, attr_logical,
            "--yes",
        ])
        assert r_del.returncode == 0, r_del.stderr
        env_del = json.loads(r_del.stdout)
        assert env_del["ok"], env_del
        assert env_del["data"].get("deleted") is True

    except Exception:
        # Best-effort: delete the attribute if something went wrong before step 3.
        cli([
            "--json", "metadata", "delete-attribute", ephemeral_entity, attr_logical,
            "--yes",
        ], check=False)
        raise


# ---------------------------------------------------------------------------
# metadata create-many-to-many  +  metadata delete-relationship (lifecycle)
# ---------------------------------------------------------------------------

@covers("metadata create-many-to-many", "metadata delete-relationship")
@pytest.mark.slow
def test_create_and_delete_many_to_many(cli, ephemeral_entity, unique):
    """Create an N:N between ephemeral_entity and account; delete it.

    The relationship schema must use the publisher prefix 'new_' on both orgs.
    The intersect entity logical name must be unique, short enough (≤ 50 chars),
    and start with 'new_'.
    """
    # Schema names must be < 50 chars and start with publisher prefix.
    rel_schema = f"new_e2enn{unique}"
    intersect = f"new_e2exi{unique}"

    r_create = cli([
        "--json", "metadata", "create-many-to-many",
        "--schema-name", rel_schema,
        "--entity1", ephemeral_entity,
        "--entity2", "account",
        "--intersect-entity", intersect,
        "--no-publish",
    ])
    assert r_create.returncode == 0, r_create.stderr
    env_create = json.loads(r_create.stdout)
    assert env_create["ok"], env_create
    data = env_create["data"]
    assert data.get("created") is True
    assert data.get("kind") == "ManyToMany"

    try:
        # Confirm the relationship exists by reading it back.
        r_read = cli(["--json", "metadata", "relationships", ephemeral_entity])
        assert r_read.returncode == 0, r_read.stderr
        rels = json.loads(r_read.stdout)["data"]
        nn_schemas = [r.get("SchemaName") for r in rels.get("ManyToMany", [])]
        assert rel_schema in nn_schemas, (
            f"N:N {rel_schema!r} not found in ManyToMany list: {nn_schemas}"
        )

        # delete-relationship.
        r_del = cli([
            "--json", "metadata", "delete-relationship", rel_schema,
            "--yes",
        ])
        assert r_del.returncode == 0, r_del.stderr
        env_del = json.loads(r_del.stdout)
        assert env_del["ok"], env_del
        assert env_del["data"].get("deleted") is True

    except Exception:
        # Best-effort cleanup so we don't litter the org.
        cli(["--json", "metadata", "delete-relationship", rel_schema, "--yes"],
            check=False)
        raise


# ---------------------------------------------------------------------------
# metadata update-relationship  (1:N lifecycle)
# ---------------------------------------------------------------------------

@covers("metadata update-relationship")
@pytest.mark.slow
def test_update_relationship_cascade(cli, ephemeral_entity, unique):
    """Create a 1:N from account→ephemeral_entity, change ONE cascade key via
    update-relationship, then confirm from the server that the change applied
    and an untouched cascade key survived. Cleans up.

    Regression guard for #267: the command used to retrieve-merge-write through
    the typed cast path, PUTting to
    `RelationshipDefinitions(<id>)/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata`,
    which Dataverse rejects with HTTP 405 on BOTH on-prem v9.1 (0x0) and cloud
    v9.2 (0x80060888). The fix reads the merge base from the cast path but PUTs
    the full definition to the un-cast `RelationshipDefinitions(<id>)` path with
    the @odata.type discriminator in the body.
    """
    rel_schema = f"new_e2e1n{unique}"
    lookup_schema = f"new_e2elu{unique}"

    # Create a 1:N: account (referenced/1-side) → ephemeral_entity (referencing/N-side).
    # Seed two cascade keys: Assign=NoCascade (the one we will change) and
    # Delete=Restrict (a non-default key we will NOT touch — it must survive the
    # update, proving the round-trip doesn't silently reset omitted keys).
    r_create = cli([
        "--json", "metadata", "create-one-to-many",
        "--schema-name", rel_schema,
        "--referenced-entity", "account",
        "--referencing-entity", ephemeral_entity,
        "--lookup-schema", lookup_schema,
        "--lookup-display", f"E2E Account {unique}",
        "--cascade-assign", "NoCascade",
        "--cascade-delete", "Restrict",
        "--no-publish",
    ], check=False)
    created = r_create.returncode == 0

    try:
        assert r_create.returncode == 0, r_create.stderr
        env_create = json.loads(r_create.stdout)
        assert env_create["ok"], env_create
        assert env_create["data"].get("created") is True

        # update-relationship: flip ONLY cascade-assign to Cascade.
        r_upd = cli([
            "--json", "metadata", "update-relationship", rel_schema,
            "--cascade-assign", "Cascade",
            "--no-publish",
        ])
        assert r_upd.returncode == 0, r_upd.stderr
        env_upd = json.loads(r_upd.stdout)
        assert env_upd["ok"], env_upd
        assert env_upd["data"].get("updated") is True

        # Read the cascade config back from the server. export-spec surfaces it
        # via the referenced (1-side) entity's OneToManyRelationships. The
        # changed key must be applied; the untouched key must still be Restrict.
        r_read = cli([
            "--json", "metadata", "export-spec", "account",
            "--with-relationships",
        ])
        assert r_read.returncode == 0, r_read.stderr
        env_read = json.loads(r_read.stdout)
        assert env_read["ok"], env_read
        entities = env_read["data"].get("entities") or []
        rels = entities[0].get("relationships", []) if entities else []
        rel = next((x for x in rels if x.get("schema_name") == rel_schema), None)
        assert rel is not None, (
            f"relationship {rel_schema} not found in account export; saw "
            f"{[x.get('schema_name') for x in rels]}"
        )
        # export-spec emits the FLAT cascade_* keys the apply adapter consumes
        # (#597); a non-default dimension survives the round-trip without reset.
        assert rel.get("cascade_assign") == "Cascade", rel   # changed key applied
        assert rel.get("cascade_delete") == "Restrict", rel  # untouched key survives

    finally:
        if created:
            # Clean up the relationship (Dataverse deletes its lookup attribute too).
            cli(["--json", "metadata", "delete-relationship", rel_schema, "--yes"],
                check=False)


# ---------------------------------------------------------------------------
# metadata create-key  +  metadata delete-key  (alternate-key lifecycle)
# ---------------------------------------------------------------------------

@covers("metadata create-key", "metadata delete-key")
@pytest.mark.slow
def test_create_and_delete_alternate_key(cli, ephemeral_entity, unique):
    """Full alternate-key lifecycle on ephemeral_entity:
      1. add-attribute (string) to get a key-eligible column
      2. create-key on that column; assert created=True
      3. metadata keys lists the new key (existing read path)
      4. delete-key --yes; assert deleted=True
    The attribute is deleted in finally so the entity teardown is clean.
    """
    attr_schema = f"new_e2ek{unique}"
    attr_logical = attr_schema.lower()
    key_schema = f"new_e2eak{unique}"
    key_logical = key_schema.lower()

    r_add = cli([
        "--json", "metadata", "add-attribute", ephemeral_entity,
        "--kind", "string",
        "--schema-name", attr_schema,
        "--display", f"E2E Key Col {unique}",
        "--no-publish",
    ])
    assert r_add.returncode == 0, r_add.stderr
    assert json.loads(r_add.stdout)["ok"]

    try:
        # Step 2: create the alternate key on the seeded column.
        r_create = cli([
            "--json", "metadata", "create-key", ephemeral_entity,
            "--name", key_schema,
            "--key-attributes", attr_logical,
            "--display", f"E2E AK {unique}",
            "--no-publish",
        ])
        assert r_create.returncode == 0, r_create.stderr
        env_create = json.loads(r_create.stdout)
        assert env_create["ok"], env_create
        assert env_create["data"].get("created") is True

        try:
            # Step 3: the existing read path lists the new key.
            r_keys = cli(["--json", "metadata", "keys", ephemeral_entity])
            assert r_keys.returncode == 0, r_keys.stderr
            keys = json.loads(r_keys.stdout)["data"]
            assert any(k.get("logical_name") == key_logical for k in keys), (
                f"key {key_logical!r} not found in keys list: {keys}"
            )

            # Step 4: delete the key.
            r_del = cli([
                "--json", "metadata", "delete-key", ephemeral_entity, key_logical,
                "--yes",
            ])
            assert r_del.returncode == 0, r_del.stderr
            env_del = json.loads(r_del.stdout)
            assert env_del["ok"], env_del
            assert env_del["data"].get("deleted") is True
        except Exception:
            cli(["--json", "metadata", "delete-key", ephemeral_entity, key_logical,
                 "--yes"], check=False)
            raise
    finally:
        cli(["--json", "metadata", "delete-attribute", ephemeral_entity,
             attr_logical, "--yes"], check=False)


# ---------------------------------------------------------------------------
# metadata clone-entity
# ---------------------------------------------------------------------------

@covers("metadata clone-entity")
@pytest.mark.slow
def test_clone_entity(cli, ephemeral_entity, unique):
    """Clone ephemeral_entity into a uniquely-named entity; assert created;
    delete the clone in finally."""
    clone_schema = f"new_E2EClone{unique}"
    clone_logical = clone_schema.lower()

    r = cli([
        "--json", "metadata", "clone-entity",
        ephemeral_entity, clone_schema,
        "--display", f"E2E Clone {unique}",
        "--no-publish",
    ])
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("created") is True

    try:
        # Confirm clone exists in the entity list.
        r2 = cli(["--json", "metadata", "entities"])
        assert r2.returncode == 0, r2.stderr
        items = json.loads(r2.stdout)["data"]
        logical_names = [it.get("LogicalName") for it in items]
        assert clone_logical in logical_names, (
            f"Clone {clone_logical!r} not found in entity list"
        )
    finally:
        # Delete the clone; use --yes to skip the interactive confirmation.
        cli([
            "--json", "metadata", "delete-entity", clone_logical, "--yes",
        ], check=False)
