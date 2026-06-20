# pyright: basic
"""E2E tests for the field-mapping command (attributemap + AutoMapEntity)."""
from __future__ import annotations

from crm.tests.e2e.coverage import covers


@covers("metadata create-mapping")
def test_create_mapping_lifecycle(backend, ephemeral_entity, unique):
    """Create a 1:N account→<entity> relationship (which seeds an entity map),
    then exercise both the --auto and manual --from/--to paths."""
    from crm.core import relationships as rel
    from crm.core import mappings as mp
    from crm.core import metadata as meta_mod

    rel_schema = f"new_map{unique}_account_{ephemeral_entity}"
    rel.create_one_to_many(
        backend,
        schema_name=rel_schema,
        referenced_entity="account",
        referencing_entity=ephemeral_entity,
        lookup_schema=f"new_Map{unique}AccountId",
        lookup_display="Map Account",
        publish=True,
    )

    # --auto: bulk-generate the likely maps for the pair.
    auto = mp.auto_map(backend, rel_schema)
    assert auto["auto_mapped"] is True

    # Manual map: account.accountnumber (string, len 20) → the entity's primary
    # name attribute (string, default len 200 ≥ 20 satisfies the target-length
    # rule). Direction is fixed: account is the referenced (source) side.
    primary = meta_mod.entity_info(backend, ephemeral_entity).get("PrimaryNameAttribute")
    created = mp.create_mapping(
        backend, rel_schema, source_attr="accountnumber", target_attr=primary,
    )
    assert created["created"] is True
