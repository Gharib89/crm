# pyright: basic
"""E2E tests for metadata commands."""
from __future__ import annotations

import uuid

import pytest

from crm.tests.e2e.coverage import covers
from crm.utils.d365_backend import D365Error


@covers("metadata entities")
def test_metadata_list_entities(backend):
    # EntityDefinitions does NOT support $top server-side on v9.1 on-prem;
    # use the helper, which slices client-side.
    from crm.core import metadata as md
    items = md.list_entities(backend)
    names = [it.get("LogicalName") for it in items]
    assert "account" in names, f"Did not see 'account' in: {names[:10]}..."


@covers("metadata create-entity")
def test_e2e_create_custom_entity_reads_back_set_name(backend):
    """§3.3: create a unique custom entity, assert returned entity_set_name resolves via metadata.list_entities."""
    from crm.core import metadata as meta_mod
    suffix = uuid.uuid4().hex[:8]
    schema = f"new_SpecAReadback{suffix}"
    try:
        info = meta_mod.create_entity(
            backend,
            schema_name=schema,
            display_name=f"SpecA Readback {suffix}",
        )
        assert info["created"] is True
        assert info["entity_set_name"] is not None
        entities = meta_mod.list_entities(backend, custom_only=True)
        by_logical = {e.get("LogicalName"): e for e in entities}
        assert schema.lower() in by_logical
        server_set_name = by_logical[schema.lower()].get("EntitySetName")
        assert info["entity_set_name"] == server_set_name
    finally:
        # Best-effort cleanup; ignore failure (entity stays for manual cleanup).
        try:
            backend.delete(f"EntityDefinitions(LogicalName='{schema.lower()}')")
        except Exception:
            pass


# One param per attribute kind so a kind the SDK legitimately refuses
# records its own `xfailed` instead of failing the whole class. bigint is
# system-managed ("BigIntAttributeMetadata cannot be created through the
# SDK") — expect the server 4xx as a D365Error. multiselect/image/file may
# be feature-gated on some builds; add them here as xfail params likewise.
@covers("metadata add-attribute", "metadata delete-entity")
@pytest.mark.parametrize("kind,extra", [
    ("string", {"max_length": 100}),
    ("memo", {"max_length": 1000}),
    ("integer", {"min_value": 0, "max_value": 100}),
    pytest.param("bigint", {}, marks=pytest.mark.xfail(
        raises=D365Error, strict=False,
        reason="BigInt attributes are system-managed; not creatable through the SDK.",
    )),
    ("decimal", {"precision": 2}),
    ("double", {"precision": 3}),
    ("money", {"precision": 2}),
    ("boolean", {}),
    ("datetime", {}),
    ("picklist", {"options": [(1, "A"), (2, "B")]}),
])
def test_add_attribute_each_kind(backend, ephemeral_entity, kind, extra):
    from crm.core import metadata_attrs as ma
    info = ma.add_attribute(
        backend,
        entity=ephemeral_entity,
        kind=kind,
        schema_name=f"new_E2E{kind.capitalize()}",
        display_name=f"E2E {kind}",
        publish=False,
        **extra,
    )
    assert info.get("created") or info.get("kind") == "OneToMany", info


@covers("metadata add-attribute")
@pytest.mark.parametrize("kind,odata_type,expected_max_length", [
    ("string", "StringAttributeMetadata", 100),
    ("memo", "MemoAttributeMetadata", 2000),
])
def test_add_string_memo_without_max_length_defaults(
    backend, ephemeral_entity, kind, odata_type, expected_max_length,
):
    """#321: a string/memo column created with no max_length must succeed at real
    apply (not just dry-run) and store the kind default (100/2000) server-side."""
    from crm.core import metadata_attrs as ma
    schema = f"new_E2EDefault{kind.capitalize()}"
    info = ma.add_attribute(
        backend,
        entity=ephemeral_entity,
        kind=kind,
        schema_name=schema,
        display_name=f"E2E default {kind}",
        publish=False,
    )
    assert info.get("created"), info
    # MaxLength lives on the derived attribute type, not the base AttributeMetadata,
    # so the read-back must cast to it.
    rb = backend.get(
        f"EntityDefinitions(LogicalName='{ephemeral_entity}')"
        f"/Attributes(LogicalName='{schema.lower()}')"
        f"/Microsoft.Dynamics.CRM.{odata_type}",
        params={"$select": "MaxLength"},
    )
    assert rb["MaxLength"] == expected_max_length


@covers("metadata add-attribute")
@pytest.mark.parametrize("behavior,expected_format", [
    ("TimeZoneIndependent", "DateAndTime"),  # non-default, format untouched
    ("DateOnly", "DateOnly"),                # format auto-defaults to DateOnly
])
def test_add_datetime_with_behavior(backend, ephemeral_entity, behavior, expected_format):
    """#359: --behavior writes DateTimeBehavior; verify a non-default value is
    stored server-side (not the UserLocal default). DateOnly also exercises the
    format auto-default that Dataverse requires for DateOnly behavior."""
    from crm.core import metadata_attrs as ma
    schema = f"new_E2EBehavior{behavior}"
    info = ma.add_attribute(
        backend,
        entity=ephemeral_entity,
        kind="datetime",
        schema_name=schema,
        display_name=f"E2E behavior {behavior}",
        behavior_name=behavior,
        publish=False,
    )
    assert info.get("created"), info
    rb = backend.get(
        f"EntityDefinitions(LogicalName='{ephemeral_entity}')"
        f"/Attributes(LogicalName='{schema.lower()}')"
        f"/Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
        params={"$select": "DateTimeBehavior,Format"},
    )
    assert rb["DateTimeBehavior"]["Value"] == behavior
    assert rb["Format"] == expected_format


@covers("metadata add-attribute")
def test_add_customer_attribute_targets_account_and_contact(backend, ephemeral_entity):
    """#367: --kind customer creates a Customer composite lookup via the
    CreateCustomerRelationships action; the column's Targets must be exactly
    account + contact."""
    from crm.core import metadata_attrs as ma
    schema = "new_E2ECustomer"
    info = ma.add_attribute(
        backend,
        entity=ephemeral_entity,
        kind="customer",
        schema_name=schema,
        display_name="E2E customer",
        publish=False,
    )
    assert info.get("created"), info
    assert info["targets"] == ["account", "contact"]
    rb = backend.get(
        f"EntityDefinitions(LogicalName='{ephemeral_entity}')"
        f"/Attributes(LogicalName='{schema.lower()}')"
        f"/Microsoft.Dynamics.CRM.LookupAttributeMetadata",
        params={"$select": "Targets"},
    )
    assert sorted(rb["Targets"]) == ["account", "contact"]
