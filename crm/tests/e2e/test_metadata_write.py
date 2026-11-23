# pyright: basic
"""E2E tests for metadata WRITE verbs:
  - metadata update-entity
  - metadata update-attribute
  - metadata delete-attribute
  - metadata create-many-to-many  (+ metadata delete-relationship)
  - metadata update-relationship  (uses a 1:N created in the same lifecycle test)
  - metadata clone-entity.

All tests use `ephemeral_entity` as the base so they never pay the
create+publish cost themselves. Each test cleans up exactly what it creates;
shared session state is left to the conftest session-scope fixture.
"""

from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


@covers("metadata update-entity")
@pytest.mark.slow
def test_update_entity_display_name(cli, ephemeral_entity, ephemeral_solution):
    """Update the ephemeral entity's display name and assert the change is
    read back from the server.
    """
    new_display = f"E2E Updated {ephemeral_entity}"
    r = cli(
        [
            "--json",
            "metadata",
            "update-entity",
            ephemeral_entity,
            "--display",
            new_display,
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("updated") is True
    assert data.get("logical_name") == ephemeral_entity

    r2 = cli(["--json", "metadata", "entity", ephemeral_entity])
    assert r2.returncode == 0, r2.stderr
    env2 = json.loads(r2.stdout)
    assert env2["ok"]
    dn = env2["data"].get("DisplayName") or {}
    ull = dn.get("UserLocalizedLabel") or {}
    assert ull.get("Label") == new_display, f"Expected '{new_display}', got: {json.dumps(dn)}"


@covers("metadata update-entity")
@pytest.mark.slow
def test_update_entity_description(cli, ephemeral_entity, ephemeral_solution):
    """Update description; confirm updated=True (no redundant readback needed since
    test_update_entity_display_name already exercises the GET path).
    """
    r = cli(
        [
            "--json",
            "metadata",
            "update-entity",
            ephemeral_entity,
            "--description",
            "E2E description set by test_update_entity_description",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"], env
    assert env["data"].get("updated") is True


@covers("metadata update-attribute", "metadata delete-attribute")
@pytest.mark.slow
def test_update_and_delete_string_attribute(cli, ephemeral_entity, unique, ephemeral_solution):
    """Full attribute lifecycle on ephemeral_entity:
      1. add-attribute (string) to get a test column
      2. update-attribute --display / --max-length; assert updated=True
      3. delete-attribute --yes; assert deleted=True
    Cleanup in finally ensures the attribute is gone even if assertions fail.
    """
    attr_schema = f"new_e2etest{unique}"
    attr_logical = attr_schema.lower()

    r_add = cli(
        [
            "--json",
            "metadata",
            "add-attribute",
            ephemeral_entity,
            "--kind",
            "string",
            "--schema-name",
            attr_schema,
            "--display",
            f"E2E Test {unique}",
            "--max-length",
            "150",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r_add.returncode == 0, r_add.stderr
    env_add = json.loads(r_add.stdout)
    assert env_add["ok"], env_add

    try:
        r_upd = cli(
            [
                "--json",
                "metadata",
                "update-attribute",
                ephemeral_entity,
                attr_logical,
                "--display",
                f"E2E Updated {unique}",
                "--max-length",
                "200",
                "--no-publish",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_upd.returncode == 0, r_upd.stderr
        env_upd = json.loads(r_upd.stdout)
        assert env_upd["ok"], env_upd
        assert env_upd["data"].get("updated") is True

        r_read = cli(["--json", "metadata", "attribute", ephemeral_entity, attr_logical])
        assert r_read.returncode == 0, r_read.stderr
        attr_data = json.loads(r_read.stdout)["data"]
        assert attr_data.get("MaxLength") == 200, (
            f"Expected MaxLength=200, got: {attr_data.get('MaxLength')}"
        )

        r_del = cli(
            [
                "--json",
                "metadata",
                "delete-attribute",
                ephemeral_entity,
                attr_logical,
                "--yes",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_del.returncode == 0, r_del.stderr
        env_del = json.loads(r_del.stdout)
        assert env_del["ok"], env_del
        assert env_del["data"].get("deleted") is True

    except Exception:
        cli(
            [
                "--json",
                "metadata",
                "delete-attribute",
                ephemeral_entity,
                attr_logical,
                "--yes",
                "--solution",
                ephemeral_solution,
            ],
            check=False,
        )
        raise


@covers("metadata update-attribute")
@pytest.mark.slow
def test_update_integer_attribute_bounds(cli, ephemeral_entity, unique, ephemeral_solution):
    """Integer --min/--max updates serialize as whole numbers accepted by Dataverse."""
    attr_schema = f"new_e2eint{unique}"
    attr_logical = attr_schema.lower()

    r_add = cli(
        [
            "--json",
            "metadata",
            "add-attribute",
            ephemeral_entity,
            "--kind",
            "integer",
            "--schema-name",
            attr_schema,
            "--display",
            f"E2E Int {unique}",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r_add.returncode == 0, r_add.stderr
    env_add = json.loads(r_add.stdout)
    assert env_add["ok"], env_add

    try:
        r_upd = cli(
            [
                "--json",
                "metadata",
                "update-attribute",
                ephemeral_entity,
                attr_logical,
                "--min",
                "0",
                "--max",
                "100",
                "--no-publish",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_upd.returncode == 0, r_upd.stderr
        env_upd = json.loads(r_upd.stdout)
        assert env_upd["ok"], env_upd

        r_read = cli(["--json", "metadata", "attribute", ephemeral_entity, attr_logical])
        assert r_read.returncode == 0, r_read.stderr
        attr_data = json.loads(r_read.stdout)["data"]
        assert attr_data.get("MinValue") == 0
        assert attr_data.get("MaxValue") == 100
    finally:
        cli(
            [
                "--json",
                "metadata",
                "delete-attribute",
                ephemeral_entity,
                attr_logical,
                "--yes",
                "--solution",
                ephemeral_solution,
            ],
            check=False,
        )


@covers("metadata update-attribute")
@pytest.mark.slow
def test_update_datetime_behavior_userlocal_to_dateonly(
    cli, ephemeral_entity, unique, ephemeral_solution
):
    """The one-time DateTimeBehavior flip on a datetime column (#817):
    1. add-attribute datetime (defaults to UserLocal / DateAndTime)
    2. update-attribute --behavior DateOnly; assert updated=True and the
       no-backfill advisory rides meta.warnings
    3. read back: server reports DateTimeBehavior=DateOnly and the coupled
       Format=DateOnly (auto-set).

    The test first asserts a fresh custom datetime column starts UserLocal, the
    only state from which a behavior change is legal.
    """
    attr_schema = f"new_e2edt{unique}"
    attr_logical = attr_schema.lower()

    r_add = cli(
        [
            "--json",
            "metadata",
            "add-attribute",
            ephemeral_entity,
            "--kind",
            "datetime",
            "--schema-name",
            attr_schema,
            "--display",
            f"E2E DT {unique}",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r_add.returncode == 0, r_add.stderr
    assert json.loads(r_add.stdout)["ok"], r_add.stdout

    def _behavior(attr):
        b = attr.get("DateTimeBehavior")
        return b.get("Value") if isinstance(b, dict) else b

    try:
        r0 = cli(["--json", "metadata", "attribute", ephemeral_entity, attr_logical])
        assert r0.returncode == 0, r0.stderr
        assert _behavior(json.loads(r0.stdout)["data"]) == "UserLocal"

        r_upd = cli(
            [
                "--json",
                "metadata",
                "update-attribute",
                ephemeral_entity,
                attr_logical,
                "--behavior",
                "DateOnly",
                "--no-publish",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_upd.returncode == 0, r_upd.stderr
        env_upd = json.loads(r_upd.stdout)
        assert env_upd["ok"], env_upd
        assert env_upd["data"].get("updated") is True
        warnings = (env_upd.get("meta") or {}).get("warnings") or []
        assert any("ConvertDateAndTimeBehavior" in w for w in warnings), env_upd

        r_read = cli(["--json", "metadata", "attribute", ephemeral_entity, attr_logical])
        assert r_read.returncode == 0, r_read.stderr
        attr_data = json.loads(r_read.stdout)["data"]
        assert _behavior(attr_data) == "DateOnly", attr_data.get("DateTimeBehavior")
        assert attr_data.get("Format") == "DateOnly", attr_data.get("Format")

    finally:
        cli(
            [
                "--json",
                "metadata",
                "delete-attribute",
                ephemeral_entity,
                attr_logical,
                "--yes",
                "--solution",
                ephemeral_solution,
            ],
            check=False,
        )


@covers("metadata add-attribute", "metadata update-attribute", "metadata update-entity")
@pytest.mark.slow
def test_audit_enablement_on_attribute_and_entity(
    cli, ephemeral_entity, unique, ephemeral_solution
):
    """--audit/--no-audit toggles the IsAuditEnabled managed property, read back
    from the server on both a column and the table.

    Setting the metadata property is independent of org-level auditing (that only
    gates whether audit *records* are written), so the read-back reflects the
    flag regardless of the org's audit setting.

    Toggling the column off through update-attribute also asserts the
    type-specific MaxLength is unchanged, which proves the retrieve-merge-write
    kept it intact.
    """
    attr_schema = f"new_e2eaudit{unique}"
    attr_logical = attr_schema.lower()

    r_add = cli(
        [
            "--json",
            "metadata",
            "add-attribute",
            ephemeral_entity,
            "--kind",
            "string",
            "--schema-name",
            attr_schema,
            "--display",
            f"E2E Audit {unique}",
            "--audit",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r_add.returncode == 0, r_add.stderr
    assert json.loads(r_add.stdout)["ok"], r_add.stdout

    try:
        r_read = cli(["--json", "metadata", "attribute", ephemeral_entity, attr_logical])
        assert r_read.returncode == 0, r_read.stderr
        col = json.loads(r_read.stdout)["data"]
        assert (col.get("IsAuditEnabled") or {}).get("Value") is True, (
            f"Expected IsAuditEnabled.Value=True, got: {col.get('IsAuditEnabled')}"
        )

        r_upd = cli(
            [
                "--json",
                "metadata",
                "update-attribute",
                ephemeral_entity,
                attr_logical,
                "--no-audit",
                "--no-publish",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_upd.returncode == 0, r_upd.stderr
        assert json.loads(r_upd.stdout)["data"].get("updated") is True

        r_read2 = cli(["--json", "metadata", "attribute", ephemeral_entity, attr_logical])
        col2 = json.loads(r_read2.stdout)["data"]
        assert (col2.get("IsAuditEnabled") or {}).get("Value") is False, (
            f"Expected IsAuditEnabled.Value=False, got: {col2.get('IsAuditEnabled')}"
        )
        assert col2.get("MaxLength") == 100, col2.get("MaxLength")

        r_ent = cli(
            [
                "--json",
                "metadata",
                "update-entity",
                ephemeral_entity,
                "--audit",
                "--no-publish",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_ent.returncode == 0, r_ent.stderr
        assert json.loads(r_ent.stdout)["data"].get("updated") is True

        r_ent_read = cli(["--json", "metadata", "entity", ephemeral_entity])
        ent = json.loads(r_ent_read.stdout)["data"]
        assert (ent.get("IsAuditEnabled") or {}).get("Value") is True, (
            f"Expected entity IsAuditEnabled.Value=True, got: {ent.get('IsAuditEnabled')}"
        )
    finally:
        cli(
            [
                "--json",
                "metadata",
                "delete-attribute",
                ephemeral_entity,
                attr_logical,
                "--yes",
                "--solution",
                ephemeral_solution,
            ],
            check=False,
        )


@covers("metadata create-many-to-many", "metadata delete-relationship")
@pytest.mark.slow
def test_create_and_delete_many_to_many(cli, ephemeral_entity, unique, ephemeral_solution):
    """Create an N:N between ephemeral_entity and account; delete it.

    The relationship schema must use the publisher prefix 'new_' on both orgs.
    The intersect entity logical name must be unique, short enough (≤ 50 chars),
    and start with 'new_'.
    """
    rel_schema = f"new_e2enn{unique}"
    intersect = f"new_e2exi{unique}"

    r_create = cli(
        [
            "--json",
            "metadata",
            "create-many-to-many",
            "--schema-name",
            rel_schema,
            "--entity1",
            ephemeral_entity,
            "--entity2",
            "account",
            "--intersect-entity",
            intersect,
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r_create.returncode == 0, r_create.stderr
    env_create = json.loads(r_create.stdout)
    assert env_create["ok"], env_create
    data = env_create["data"]
    assert data.get("created") is True
    assert data.get("kind") == "ManyToMany"

    try:
        r_read = cli(["--json", "metadata", "relationships", ephemeral_entity])
        assert r_read.returncode == 0, r_read.stderr
        rels = json.loads(r_read.stdout)["data"]
        nn_schemas = [r.get("SchemaName") for r in rels.get("ManyToMany", [])]
        assert rel_schema in nn_schemas, (
            f"N:N {rel_schema!r} not found in ManyToMany list: {nn_schemas}"
        )

        r_del = cli(
            [
                "--json",
                "metadata",
                "delete-relationship",
                rel_schema,
                "--yes",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_del.returncode == 0, r_del.stderr
        env_del = json.loads(r_del.stdout)
        assert env_del["ok"], env_del
        assert env_del["data"].get("deleted") is True

    except Exception:
        cli(
            [
                "--json",
                "metadata",
                "delete-relationship",
                rel_schema,
                "--yes",
                "--solution",
                ephemeral_solution,
            ],
            check=False,
        )
        raise


@covers("metadata update-relationship")
@pytest.mark.slow
def test_update_relationship_cascade(cli, ephemeral_entity, unique, ephemeral_solution):
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

    The 1:N is seeded with two cascade settings: Assign as NoCascade (the one
    changed) and Delete as Restrict (a non-default one left untouched): the untouched key
    must survive the update, proving the round-trip does not silently reset
    omitted keys. The read-back goes through export-spec on the referenced
    (1-side) account, whose OneToManyRelationships surface the cascade config;
    export-spec emits the flat cascade_* keys the apply adapter consumes (#597).
    Deleting the relationship in cleanup also deletes its lookup attribute.
    """
    rel_schema = f"new_e2e1n{unique}"
    lookup_schema = f"new_e2elu{unique}"

    r_create = cli(
        [
            "--json",
            "metadata",
            "create-one-to-many",
            "--schema-name",
            rel_schema,
            "--referenced-entity",
            "account",
            "--referencing-entity",
            ephemeral_entity,
            "--lookup-schema",
            lookup_schema,
            "--lookup-display",
            f"E2E Account {unique}",
            "--cascade-assign",
            "NoCascade",
            "--cascade-delete",
            "Restrict",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ],
        check=False,
    )
    created = r_create.returncode == 0

    try:
        assert r_create.returncode == 0, r_create.stderr
        env_create = json.loads(r_create.stdout)
        assert env_create["ok"], env_create
        assert env_create["data"].get("created") is True

        r_upd = cli(
            [
                "--json",
                "metadata",
                "update-relationship",
                rel_schema,
                "--cascade-assign",
                "Cascade",
                "--no-publish",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_upd.returncode == 0, r_upd.stderr
        env_upd = json.loads(r_upd.stdout)
        assert env_upd["ok"], env_upd
        assert env_upd["data"].get("updated") is True

        r_read = cli(
            [
                "--json",
                "metadata",
                "export-spec",
                "account",
                "--with-relationships",
            ]
        )
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
        assert rel.get("cascade_assign") == "Cascade", rel
        assert rel.get("cascade_delete") == "Restrict", rel

    finally:
        if created:
            cli(
                [
                    "--json",
                    "metadata",
                    "delete-relationship",
                    rel_schema,
                    "--yes",
                    "--solution",
                    ephemeral_solution,
                ],
                check=False,
            )


@covers("metadata create-key", "metadata delete-key")
@pytest.mark.slow
def test_create_and_delete_alternate_key(cli, ephemeral_entity, unique, ephemeral_solution):
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

    r_add = cli(
        [
            "--json",
            "metadata",
            "add-attribute",
            ephemeral_entity,
            "--kind",
            "string",
            "--schema-name",
            attr_schema,
            "--display",
            f"E2E Key Col {unique}",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r_add.returncode == 0, r_add.stderr
    assert json.loads(r_add.stdout)["ok"]

    try:
        r_create = cli(
            [
                "--json",
                "metadata",
                "create-key",
                ephemeral_entity,
                "--name",
                key_schema,
                "--key-attributes",
                attr_logical,
                "--display",
                f"E2E AK {unique}",
                "--no-publish",
                "--solution",
                ephemeral_solution,
            ]
        )
        assert r_create.returncode == 0, r_create.stderr
        env_create = json.loads(r_create.stdout)
        assert env_create["ok"], env_create
        assert env_create["data"].get("created") is True

        try:
            r_keys = cli(["--json", "metadata", "keys", ephemeral_entity])
            assert r_keys.returncode == 0, r_keys.stderr
            keys = json.loads(r_keys.stdout)["data"]
            assert any(k.get("logical_name") == key_logical for k in keys), (
                f"key {key_logical!r} not found in keys list: {keys}"
            )

            r_del = cli(
                [
                    "--json",
                    "metadata",
                    "delete-key",
                    ephemeral_entity,
                    key_logical,
                    "--yes",
                    "--solution",
                    ephemeral_solution,
                ]
            )
            assert r_del.returncode == 0, r_del.stderr
            env_del = json.loads(r_del.stdout)
            assert env_del["ok"], env_del
            assert env_del["data"].get("deleted") is True
        except Exception:
            cli(
                [
                    "--json",
                    "metadata",
                    "delete-key",
                    ephemeral_entity,
                    key_logical,
                    "--yes",
                    "--solution",
                    ephemeral_solution,
                ],
                check=False,
            )
            raise
    finally:
        cli(
            [
                "--json",
                "metadata",
                "delete-attribute",
                ephemeral_entity,
                attr_logical,
                "--yes",
                "--solution",
                ephemeral_solution,
            ],
            check=False,
        )


@covers("metadata clone-entity")
@pytest.mark.slow
def test_clone_entity(cli, ephemeral_entity, unique, ephemeral_solution):
    """Clone ephemeral_entity into a uniquely-named entity; assert created;
    delete the clone in finally.
    """
    clone_schema = f"new_E2EClone{unique}"
    clone_logical = clone_schema.lower()

    r = cli(
        [
            "--json",
            "metadata",
            "clone-entity",
            ephemeral_entity,
            clone_schema,
            "--display",
            f"E2E Clone {unique}",
            "--no-publish",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("created") is True

    try:
        r2 = cli(["--json", "metadata", "entities"])
        assert r2.returncode == 0, r2.stderr
        items = json.loads(r2.stdout)["data"]
        logical_names = [it.get("LogicalName") for it in items]
        assert clone_logical in logical_names, f"Clone {clone_logical!r} not found in entity list"
    finally:
        cli(
            [
                "--json",
                "metadata",
                "delete-entity",
                clone_logical,
                "--yes",
                "--solution",
                ephemeral_solution,
            ],
            check=False,
        )
