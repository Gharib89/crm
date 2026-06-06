# pyright: basic
"""Tests: cache is invalidated on successful metadata writes, not on dry-run/skip."""

from __future__ import annotations

import pytest
import requests_mock as requests_mock_module

from crm.utils.d365_backend import ConnectionProfile, D365Backend

_ENTITY_ID = "aaaa0001-0000-0000-0000-000000000000"
_ATTR_ID = "bbbb0002-0000-0000-0000-000000000000"
_OS_ID = "cccc0003-0000-0000-0000-000000000000"
_REL_ID = "dddd0004-0000-0000-0000-000000000000"


@pytest.fixture
def profile(tmp_path, monkeypatch) -> ConnectionProfile:
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


@pytest.fixture
def dry_backend(profile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=True)


def _seed_cache(profile: ConnectionProfile) -> None:
    """Write a cache file and assert it exists."""
    from crm.core import metadata_cache as mc
    mc.write_definitions(profile, [{"logical": "account", "set_name": "accounts"}], now=0.0)
    assert mc.cache_file(profile).exists()


def _cache_gone(profile: ConnectionProfile) -> bool:
    from crm.core import metadata_cache as mc
    return not mc.cache_file(profile).exists()


# ---------------------------------------------------------------------------
# metadata.py: create_entity
# ---------------------------------------------------------------------------

class TestCreateEntityInvalidatesCache:
    def test_cache_busted_on_real_create(self, profile, backend):
        from crm.core import metadata as meta_mod
        _seed_cache(profile)
        entity_url = backend.url_for(f"EntityDefinitions({_ENTITY_ID})")
        with requests_mock_module.Mocker() as m:
            # existence probe: 404 = not present
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                headers={"OData-EntityId": entity_url},
            )
            # read-back of the created entity
            m.get(
                entity_url,
                json={"EntitySetName": "new_widgets", "LogicalName": "new_widget"},
            )
            meta_mod.create_entity(backend, schema_name="new_Widget",
                                   display_name="Widget")
        assert _cache_gone(profile)

    def test_no_cache_bust_on_dry_run(self, profile, dry_backend):
        from crm.core import metadata as meta_mod
        _seed_cache(profile)
        with requests_mock_module.Mocker() as m:
            # existence probe even in dry-run (target_exists bypasses dry_run)
            m.get(
                dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            # POST returns dry-run dict
            m.post(
                dry_backend.url_for("EntityDefinitions"),
                json={"_dry_run": True},
                status_code=200,
            )
            meta_mod.create_entity(dry_backend, schema_name="new_Widget",
                                   display_name="Widget")
        from crm.core import metadata_cache as mc
        assert mc.cache_file(profile).exists(), "cache must survive a dry-run"


# ---------------------------------------------------------------------------
# metadata.py: delete_entity
# ---------------------------------------------------------------------------

class TestDeleteEntityInvalidatesCache:
    def test_cache_busted_on_real_delete(self, profile, backend):
        from crm.core import metadata as meta_mod
        _seed_cache(profile)
        with requests_mock_module.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"IsCustomEntity": True, "IsManaged": False},
            )
            m.delete(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                status_code=204,
            )
            meta_mod.delete_entity(backend, "new_widget")
        assert _cache_gone(profile)

    def test_no_cache_bust_on_dry_run_delete(self, profile, dry_backend):
        from crm.core import metadata as meta_mod
        _seed_cache(profile)
        with requests_mock_module.Mocker() as m:
            m.get(
                dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"IsCustomEntity": True, "IsManaged": False},
            )
            # DELETE in dry-run returns a preview dict
            m.delete(
                dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"_dry_run": True, "would_delete": True},
                status_code=200,
            )
            meta_mod.delete_entity(dry_backend, "new_widget")
        from crm.core import metadata_cache as mc
        assert mc.cache_file(profile).exists(), "cache must survive a dry-run delete"


# ---------------------------------------------------------------------------
# metadata_attrs.py: add_attribute
# ---------------------------------------------------------------------------

class TestAddAttributeInvalidatesCache:
    def test_cache_busted_on_real_add(self, profile, backend):
        from crm.core import metadata_attrs as ma
        _seed_cache(profile)
        attr_url = backend.url_for(
            f"EntityDefinitions(LogicalName='new_widget')/Attributes({_ATTR_ID})"
        )
        with requests_mock_module.Mocker() as m:
            # existence probe: 404
            m.get(
                backend.url_for(
                    "EntityDefinitions(LogicalName='new_widget')"
                    "/Attributes(LogicalName='new_color')"
                ),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')/Attributes"),
                status_code=204,
                headers={"OData-EntityId": attr_url},
            )
            m.get(
                attr_url,
                json={"LogicalName": "new_color", "SchemaName": "new_Color",
                      "AttributeType": "String"},
            )
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Color",
                display_name="Color",
                max_length=100,
            )
        assert _cache_gone(profile)


# ---------------------------------------------------------------------------
# metadata_update.py: update_entity
# ---------------------------------------------------------------------------

class TestUpdateEntityInvalidatesCache:
    def test_cache_busted_on_real_update(self, profile, backend):
        from crm.core import metadata_update as mu
        _seed_cache(profile)
        path = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with requests_mock_module.Mocker() as m:
            m.get(path, json={
                "@odata.type": "#Microsoft.Dynamics.CRM.EntityMetadata",
                "MetadataId": _ENTITY_ID,
                "SchemaName": "new_Widget",
                "LogicalName": "new_widget",
                "DisplayName": {"LocalizedLabels": [{"Label": "Widget", "LanguageCode": 1033}]},
                "DisplayCollectionName": {"LocalizedLabels": [{"Label": "Widgets", "LanguageCode": 1033}]},
                "OwnershipType": "UserOwned",
            })
            m.put(path, status_code=204)
            mu.update_entity(backend, "new_widget", display_name="Widget v2")
        assert _cache_gone(profile)

    def test_no_cache_bust_on_dry_run_update(self, profile, dry_backend):
        from crm.core import metadata_update as mu
        _seed_cache(profile)
        path = dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with requests_mock_module.Mocker() as m:
            # _read bypasses dry_run for GET
            m.get(path, json={
                "@odata.type": "#Microsoft.Dynamics.CRM.EntityMetadata",
                "MetadataId": _ENTITY_ID,
                "SchemaName": "new_Widget",
                "LogicalName": "new_widget",
                "DisplayName": {"LocalizedLabels": [{"Label": "Widget", "LanguageCode": 1033}]},
                "DisplayCollectionName": {"LocalizedLabels": [{"Label": "Widgets", "LanguageCode": 1033}]},
                "OwnershipType": "UserOwned",
            })
            mu.update_entity(dry_backend, "new_widget", display_name="Widget v2")
        from crm.core import metadata_cache as mc
        assert mc.cache_file(profile).exists(), "cache must survive a dry-run update"


# ---------------------------------------------------------------------------
# optionsets.py: create_optionset
# ---------------------------------------------------------------------------

class TestCreateOptionsetInvalidatesCache:
    def test_cache_busted_on_real_create(self, profile, backend):
        from crm.core import optionsets as os_mod
        _seed_cache(profile)
        os_url = backend.url_for(f"GlobalOptionSetDefinitions({_OS_ID})")
        with requests_mock_module.Mocker() as m:
            # existence probe: 404
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("GlobalOptionSetDefinitions"),
                status_code=204,
                headers={"OData-EntityId": os_url},
            )
            m.get(
                os_url,
                json={"Name": "new_priority", "IsCustomOptionSet": True},
            )
            os_mod.create_optionset(backend, name="new_priority",
                                    display_name="Priority")
        assert _cache_gone(profile)


# ---------------------------------------------------------------------------
# relationships.py: create_one_to_many
# ---------------------------------------------------------------------------

class TestCreateOneToManyInvalidatesCache:
    def test_cache_busted_on_real_create(self, profile, backend):
        from crm.core import relationships as rel
        _seed_cache(profile)
        rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock_module.Mocker() as m:
            # existence probe: 404
            m.get(
                backend.url_for(
                    "RelationshipDefinitions(SchemaName='new_account_new_widget')"
                ),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("RelationshipDefinitions"),
                status_code=204,
                headers={"OData-EntityId": rel_url},
            )
            m.get(
                rel_url + "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
                json={"SchemaName": "new_account_new_widget",
                      "ReferencingAttribute": "new_accountid"},
            )
            rel.create_one_to_many(
                backend,
                schema_name="new_account_new_widget",
                referenced_entity="account",
                referencing_entity="new_widget",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )
        assert _cache_gone(profile)


# ---------------------------------------------------------------------------
# solution.py: publish_all
# ---------------------------------------------------------------------------

class TestPublishAllInvalidatesCache:
    def test_cache_busted_on_real_publish(self, profile, backend):
        from crm.core import solution as sol_mod
        _seed_cache(profile)
        with requests_mock_module.Mocker() as m:
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            sol_mod.publish_all(backend)
        assert _cache_gone(profile)

    def test_no_cache_bust_on_dry_run_publish(self, profile, dry_backend):
        from crm.core import solution as sol_mod
        _seed_cache(profile)
        with requests_mock_module.Mocker() as m:
            # dry-run POST returns preview dict (truthy)
            m.post(
                dry_backend.url_for("PublishAllXml"),
                json={"_dry_run": True},
                status_code=200,
            )
            sol_mod.publish_all(dry_backend)
        from crm.core import metadata_cache as mc
        assert mc.cache_file(profile).exists(), "cache must survive dry-run publish"
