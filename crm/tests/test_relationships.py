"""Unit tests for crm.core.relationships."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_REL_ID = "22222222-2222-2222-2222-222222222222"


class TestCreateOneToMany:
    def test_happy_path_posts_action_and_reads_back(self, backend):
        from crm.core import relationships as rel
        rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("RelationshipDefinitions(SchemaName='new_account_new_project')"),
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
                json={"SchemaName": "new_account_new_project",
                      "ReferencingAttribute": "new_accountid"},
            )
            info = rel.create_one_to_many(
                backend,
                schema_name="new_account_new_project",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )
        assert info["created"] is True
        assert info["kind"] == "OneToMany"
        assert info["schema_name"] == "new_account_new_project"
        assert info["referencing_attribute"] == "new_accountid"
        assert info["relationship_id"] == _REL_ID
        # POST goes to RelationshipDefinitions with the metadata inline and the
        # lookup as a Lookup deep insert; verify the type discriminator and cascade.
        body = next(r for r in m.request_history if r.method == "POST").json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        assert body["Lookup"]["SchemaName"] == "new_AccountId"
        # Default associated-menu behavior uses the collection name (valid without a
        # custom label); UseLabel without a label is rejected by the server.
        assert body["AssociatedMenuConfiguration"]["Behavior"] == "UseCollectionName"
        cc = body["CascadeConfiguration"]
        assert cc["Delete"] == "RemoveLink"
        assert cc["Assign"] == "NoCascade"

    def test_use_label_without_label_rejected(self, backend):
        from crm.core import relationships as rel
        with pytest.raises(D365Error, match="UseLabel"):
            rel.create_one_to_many(
                backend,
                schema_name="new_account_new_project",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
                menu_behavior="UseLabel",
            )

    def test_rejects_schema_without_prefix(self, backend):
        from crm.core import relationships as rel
        with pytest.raises(D365Error, match="publisher prefix"):
            rel.create_one_to_many(
                backend,
                schema_name="bad",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )

    def test_rejects_bad_cascade_value(self, backend):
        from crm.core import relationships as rel
        with pytest.raises(D365Error, match="cascade_delete"):
            rel.create_one_to_many(
                backend,
                schema_name="new_a_b",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
                cascade_delete="BogusValue",
            )

    def test_readback_failure_non_fatal(self, backend):
        from crm.core import relationships as rel
        rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("RelationshipDefinitions(SchemaName='new_a_b')"),
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
                status_code=500, json={"error": {"message": "boom"}},
            )
            info = rel.create_one_to_many(
                backend,
                schema_name="new_a_b",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )
        assert info["created"] is True
        assert "relationship_lookup_error" in info


class TestListRelationshipsMoved:
    def test_list_relationships_works_from_new_module(self, backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')/OneToManyRelationships"),
                json={"value": [{"SchemaName": "one"}]},
            )
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')/ManyToOneRelationships"),
                json={"value": [{"SchemaName": "n_one"}]},
            )
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')/ManyToManyRelationships"),
                json={"value": [{"SchemaName": "many"}]},
            )
            result = rel.list_relationships(backend, "account")
        assert result["OneToMany"][0]["SchemaName"] == "one"
        assert result["ManyToOne"][0]["SchemaName"] == "n_one"
        assert result["ManyToMany"][0]["SchemaName"] == "many"


class TestCreateManyToMany:
    def test_happy_path(self, backend):
        from crm.core import relationships as rel
        rel_url = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("RelationshipDefinitions(SchemaName='new_account_project')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("RelationshipDefinitions"),
                status_code=204,
                headers={"OData-EntityId": rel_url},
            )
            m.get(
                rel_url + "/Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
                json={"SchemaName": "new_account_project",
                      "IntersectEntityName": "new_account_project"},
            )
            info = rel.create_many_to_many(
                backend,
                schema_name="new_account_project",
                entity1_logical="account",
                entity2_logical="new_project",
                intersect_entity="new_account_project",
            )
        assert info["created"] is True
        assert info["kind"] == "ManyToMany"
        assert info["intersect_entity"] == "new_account_project"
        body = next(r for r in m.request_history if r.method == "POST").json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata"
        assert body["IntersectEntityName"] == "new_account_project"
        assert body["Entity1LogicalName"] == "account"

    def test_rejects_self_relationship(self, backend):
        from crm.core import relationships as rel
        with pytest.raises(D365Error, match="self N:N"):
            rel.create_many_to_many(
                backend,
                schema_name="new_x_y",
                entity1_logical="new_project",
                entity2_logical="new_project",
                intersect_entity="new_xy",
            )


class TestDeleteRelationship:
    def test_refuses_non_custom(self, backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("RelationshipDefinitions(SchemaName='account_contacts')"),
                json={"SchemaName": "account_contacts",
                      "IsCustomRelationship": False, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="not a custom"):
                rel.delete_relationship(backend, "account_contacts")

    def test_refuses_managed(self, backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("RelationshipDefinitions(SchemaName='vendor_account_vendor')"),
                json={"SchemaName": "vendor_account_vendor",
                      "IsCustomRelationship": True, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="managed"):
                rel.delete_relationship(backend, "vendor_account_vendor")

    def test_happy_path_deletes_with_solution_header(self, backend):
        from crm.core import relationships as rel
        path = "RelationshipDefinitions(SchemaName='new_account_new_project')"
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(path),
                json={"SchemaName": "new_account_new_project",
                      "IsCustomRelationship": True, "IsManaged": False},
            )
            m.delete(backend.url_for(path), status_code=204)
            info = rel.delete_relationship(
                backend, "new_account_new_project", solution="DevSolution",
            )
        assert info["deleted"] is True
        assert info["schema_name"] == "new_account_new_project"
        assert info["solution"] == "DevSolution"
        delete_req = m.request_history[-1]
        assert delete_req.method == "DELETE"
        assert delete_req.headers.get("MSCRM.SolutionUniqueName") == "DevSolution"
