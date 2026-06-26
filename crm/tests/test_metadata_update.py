"""Unit tests for crm.core.metadata_update — safe retrieve-merge-write."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import D365Error


_REL_ID = "33333333-3333-3333-3333-333333333333"

# A realistic, property-rich entity definition as the server would return it.
_FULL_ENTITY = {
    "@odata.type": "#Microsoft.Dynamics.CRM.EntityMetadata",
    "MetadataId": "11111111-1111-1111-1111-111111111111",
    "SchemaName": "new_Project",
    "LogicalName": "new_project",
    "DisplayName": {
        "LocalizedLabels": [{"Label": "Project", "LanguageCode": 1033}],
        "UserLocalizedLabel": {"Label": "Project", "LanguageCode": 1033},
    },
    "DisplayCollectionName": {
        "LocalizedLabels": [{"Label": "Projects", "LanguageCode": 1033}],
    },
    "Description": {
        "LocalizedLabels": [{"Label": "A project", "LanguageCode": 1033}],
    },
    "OwnershipType": "UserOwned",
    "HasActivities": False,
    "HasNotes": True,
    "IsAuditEnabled": {"Value": True, "CanBeChanged": True},
    "IsCustomEntity": True,
}


class TestRetrieveMergeWriteEntity:
    def test_put_carries_all_original_properties_plus_change(self, backend):
        from crm.core import metadata_update as mu
        path = backend.url_for("EntityDefinitions(LogicalName='new_project')")
        with requests_mock.Mocker() as m:
            m.get(path, json=_FULL_ENTITY)
            m.put(path, status_code=204)
            mu.update_entity(backend, "new_project", display_name="Engagement",
                             publish=False)
        # Last request is the PUT.
        put_req = m.request_history[-1]
        assert put_req.method == "PUT"
        body = put_req.json()
        # No-wipe proof: every original top-level property survives.
        for key in ("SchemaName", "LogicalName", "DisplayCollectionName",
                    "Description", "OwnershipType", "HasActivities", "HasNotes",
                    "IsAuditEnabled", "IsCustomEntity"):
            assert body[key] == _FULL_ENTITY[key], key
        # New DisplayName landed.
        assert body["DisplayName"]["LocalizedLabels"][0]["Label"] == "Engagement"
        # Retrieved keys are a subset of PUT-body keys (superset guarantee).
        assert set(_FULL_ENTITY).issubset(set(body))

    def test_put_to_exact_path_with_mergelabels_header(self, backend):
        from crm.core import metadata_update as mu
        path = backend.url_for("EntityDefinitions(LogicalName='new_project')")
        with requests_mock.Mocker() as m:
            m.get(path, json=_FULL_ENTITY)
            m.put(path, status_code=204)
            mu.update_entity(backend, "new_project", display_name="Engagement")
        put_req = m.request_history[-1]
        assert put_req.method == "PUT"
        assert put_req.url == path
        assert put_req.headers.get("MSCRM.MergeLabels") == "true"

    def test_empty_changes_raises_before_any_http(self, backend):
        from crm.core import metadata_update as mu
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, status_code=500)
            with pytest.raises(D365Error, match="nothing to update"):
                mu.update_entity(backend, "new_project")
            assert m.call_count == 0

    def test_get_error_propagates_no_put(self, backend):
        from crm.core import metadata_update as mu
        path = backend.url_for("EntityDefinitions(LogicalName='nope')")
        with requests_mock.Mocker() as m:
            m.get(path, status_code=404, json={"error": {"message": "not found"}})
            put = m.put(path, status_code=204)
            with pytest.raises(D365Error):
                mu.update_entity(backend, "nope", display_name="X")
            assert put.call_count == 0


_FULL_STRING_ATTR = {
    "@odata.type": "#Microsoft.Dynamics.CRM.StringAttributeMetadata",
    "MetadataId": "22222222-2222-2222-2222-222222222222",
    "SchemaName": "new_Code",
    "LogicalName": "new_code",
    "MaxLength": 100,
    "FormatName": {"Value": "Text"},
    "DisplayName": {"LocalizedLabels": [{"Label": "Code", "LanguageCode": 1033}]},
    "Description": {"LocalizedLabels": [{"Label": "The code", "LanguageCode": 1033}]},
    "RequiredLevel": {"Value": "None", "CanBeChanged": True},
}


class TestUpdateAttribute:
    def test_changing_required_preserves_other_props_via_cast_path(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_code')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.StringAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_STRING_ATTR)
            m.get(cast, json=_FULL_STRING_ATTR)
            m.put(cast, status_code=204)
            mu.update_attribute(backend, "new_project", "new_code",
                                required="ApplicationRequired")
        put_req = m.request_history[-1]
        assert put_req.method == "PUT"
        assert put_req.url == cast
        body = put_req.json()
        # Changed.
        assert body["RequiredLevel"]["Value"] == "ApplicationRequired"
        # Preserved.
        assert body["MaxLength"] == 100
        assert body["FormatName"] == {"Value": "Text"}
        assert body["DisplayName"] == _FULL_STRING_ATTR["DisplayName"]
        assert body["Description"] == _FULL_STRING_ATTR["Description"]
        assert put_req.headers.get("MSCRM.MergeLabels") == "true"


# The un-cast (base AttributeMetadata projection) GET: Dataverse returns ONLY
# base properties here — type-specific props (MaxLength, FormatName, …) are
# absent. They are only present through the @odata.type cast path.
_BASE_ONLY_STRING_ATTR = {
    "@odata.type": "#Microsoft.Dynamics.CRM.StringAttributeMetadata",
    "MetadataId": "22222222-2222-2222-2222-222222222222",
    "SchemaName": "new_Code",
    "LogicalName": "new_code",
    "DisplayName": {"LocalizedLabels": [{"Label": "Code", "LanguageCode": 1033}]},
    "Description": {"LocalizedLabels": [{"Label": "The code", "LanguageCode": 1033}]},
    "RequiredLevel": {"Value": "None", "CanBeChanged": True},
}


class TestUpdateAttributeMergeBaseFromCastPath:
    """The merge base must come from the typed cast GET, not the un-cast GET.

    The un-cast projection omits type-specific properties (MaxLength here), so
    using it as the merge base would drop them from the full PUT and reset them
    server-side. These lock in the cast-path merge base.
    """

    def test_type_specific_prop_absent_from_uncast_survives_the_put(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_code')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.StringAttributeMetadata"
        with requests_mock.Mocker() as m:
            # Un-cast GET: NO MaxLength. Cast GET: full typed body WITH MaxLength.
            base_get = m.get(base, json=_BASE_ONLY_STRING_ATTR)
            cast_get = m.get(cast, json=_FULL_STRING_ATTR)
            m.put(cast, status_code=204)
            mu.update_attribute(backend, "new_project", "new_code",
                                display_name="Phase")
        # The cast path is read for the typed merge base...
        assert base_get.call_count == 1
        assert cast_get.call_count == 1
        put_req = m.request_history[-1]
        assert put_req.method == "PUT"
        # ...and the PUT goes to the cast path.
        assert put_req.url == cast
        body = put_req.json()
        # The type-specific MaxLength — present only in the cast body — is NOT
        # dropped. (Under the un-cast merge base it would be missing entirely.)
        assert body["MaxLength"] == 100
        assert body["FormatName"] == {"Value": "Text"}
        # The requested change still landed.
        assert body["DisplayName"]["LocalizedLabels"][0]["Label"] == "Phase"


_FULL_DATETIME_ATTR = {
    "@odata.type": "#Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
    "MetadataId": "66666666-6666-6666-6666-666666666666",
    "SchemaName": "new_Due",
    "LogicalName": "new_due",
    "Format": "DateAndTime",
    "DisplayName": {"LocalizedLabels": [{"Label": "Due", "LanguageCode": 1033}]},
    "RequiredLevel": {"Value": "None", "CanBeChanged": True},
}


class TestUpdateAttributeFormat:
    def test_datetime_format_writes_plain_format_not_formatname(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_due')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.DateTimeAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_DATETIME_ATTR)
            m.get(cast, json=_FULL_DATETIME_ATTR)
            m.put(cast, status_code=204)
            mu.update_attribute(backend, "new_project", "new_due", format_name="DateOnly")
        body = m.request_history[-1].json()
        # The datetime format change lands on the plain string `Format` property.
        assert body["Format"] == "DateOnly"
        # And does NOT write the bogus FormatName key for a datetime column.
        assert "FormatName" not in body

    def test_string_format_writes_value_wrapped_formatname(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_code')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.StringAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_STRING_ATTR)
            m.get(cast, json=_FULL_STRING_ATTR)
            m.put(cast, status_code=204)
            mu.update_attribute(backend, "new_project", "new_code", format_name="Email")
        body = m.request_history[-1].json()
        assert body["FormatName"] == {"Value": "Email"}

    def test_datetime_rejects_string_format_value_client_side(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_due')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.DateTimeAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_DATETIME_ATTR)
            put = m.put(cast, status_code=204)
            with pytest.raises(D365Error, match="datetime"):
                mu.update_attribute(backend, "new_project", "new_due", format_name="Email")
            assert put.call_count == 0

    def test_max_length_on_datetime_rejected_client_side(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_due')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.DateTimeAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_DATETIME_ATTR)
            put = m.put(cast, status_code=204)
            with pytest.raises(D365Error, match="max-length"):
                mu.update_attribute(backend, "new_project", "new_due", max_length=50)
            assert put.call_count == 0


_FULL_PICKLIST_ATTR = {
    "@odata.type": "#Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
    "MetadataId": "44444444-4444-4444-4444-444444444444",
    "SchemaName": "new_Stage",
    "LogicalName": "new_stage",
    "DisplayName": {"LocalizedLabels": [{"Label": "Stage", "LanguageCode": 1033}]},
    "RequiredLevel": {"Value": "None"},
    "OptionSet": {
        "MetadataId": "55555555-5555-5555-5555-555555555555",
        "IsGlobal": False,
        "Options": [
            {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "New", "LanguageCode": 1033}]}},
            {"Value": 2, "Label": {"LocalizedLabels": [{"Label": "Done", "LanguageCode": 1033}]}},
        ],
    },
}


class TestUpdateAttributeOptionSetCarveOut:
    def test_display_update_leaves_optionset_block_untouched(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_stage')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_PICKLIST_ATTR)
            m.get(cast, json=_FULL_PICKLIST_ATTR)
            m.put(cast, status_code=204)
            mu.update_attribute(backend, "new_project", "new_stage",
                                display_name="Phase")
        body = m.request_history[-1].json()
        assert body["DisplayName"]["LocalizedLabels"][0]["Label"] == "Phase"
        # OptionSet block is byte-for-byte the retrieved one (no option edits).
        assert body["OptionSet"] == _FULL_PICKLIST_ATTR["OptionSet"]

    def test_update_attribute_does_not_accept_option_edits(self):
        from crm.core import metadata_update as mu
        import inspect
        params = inspect.signature(mu.update_attribute).parameters
        for forbidden in ("options", "optionset_name", "insert", "delete"):
            assert forbidden not in params


_FULL_ONE_TO_MANY = {
    "@odata.type": "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
    "MetadataId": _REL_ID,
    "SchemaName": "new_account_new_project",
    "ReferencedEntity": "account",
    "ReferencingEntity": "new_project",
    "ReferencingAttribute": "new_accountid",
    "CascadeConfiguration": {
        "Assign": "NoCascade",
        "Delete": "RemoveLink",
        "Reparent": "NoCascade",
        "Share": "NoCascade",
        "Unshare": "NoCascade",
        "Merge": "NoCascade",
    },
    "AssociatedMenuConfiguration": {
        "Behavior": "UseCollectionName",
        "Group": "Details",
        "Order": 10000,
        "Label": {"LocalizedLabels": []},
    },
}


class TestUpdateRelationship:
    def test_changing_one_cascade_preserves_the_rest(self, backend):
        from crm.core import metadata_update as mu
        resolve = backend.url_for(
            "RelationshipDefinitions(SchemaName='new_account_new_project')"
        )
        # Merge base is read from the typed cast path (only the cast carries the
        # full CascadeConfiguration/AssociatedMenuConfiguration), but the PUT
        # must target the UN-cast entity-set path — Dataverse rejects a PUT to
        # the cast segment with HTTP 405 (issue #267).
        cast = backend.url_for(
            f"RelationshipDefinitions({_REL_ID})"
            "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        )
        uncast = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.get(resolve, json={
                "@odata.type": "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
                "MetadataId": _REL_ID,
                "SchemaName": "new_account_new_project",
                "RelationshipType": "OneToManyRelationship",
            })
            m.get(cast, json=_FULL_ONE_TO_MANY)
            m.put(uncast, status_code=204)
            mu.update_relationship(
                backend, "new_account_new_project",
                cascade={"Delete": "Restrict"},
            )
        put_req = m.request_history[-1]
        assert put_req.method == "PUT"
        assert put_req.url == uncast
        body = put_req.json()
        # The polymorphic RelationshipDefinitions set needs the @odata.type
        # discriminator in the PUT body to know which derived type to replace.
        assert body["@odata.type"] == \
            "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        cc = body["CascadeConfiguration"]
        assert cc["Delete"] == "Restrict"
        # All other cascade members preserved.
        assert cc["Assign"] == "NoCascade"
        assert cc["Reparent"] == "NoCascade"
        assert cc["Share"] == "NoCascade"
        assert cc["Unshare"] == "NoCascade"
        assert cc["Merge"] == "NoCascade"
        # AssociatedMenuConfiguration preserved.
        assert body["AssociatedMenuConfiguration"] == \
            _FULL_ONE_TO_MANY["AssociatedMenuConfiguration"]

    def test_hierarchical_change_merges_into_put_preserving_rest(self, backend):
        from crm.core import metadata_update as mu
        resolve = backend.url_for(
            "RelationshipDefinitions(SchemaName='new_account_new_account')"
        )
        cast = backend.url_for(
            f"RelationshipDefinitions({_REL_ID})"
            "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        )
        uncast = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        with requests_mock.Mocker() as m:
            m.get(resolve, json={
                "@odata.type": "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
                "MetadataId": _REL_ID,
                "SchemaName": "new_account_new_account",
                "RelationshipType": "OneToManyRelationship",
            })
            m.get(cast, json={**_FULL_ONE_TO_MANY, "IsHierarchical": False})
            m.put(uncast, status_code=204)
            mu.update_relationship(
                backend, "new_account_new_account",
                is_hierarchical=True,
            )
        body = m.request_history[-1].json()
        assert body["IsHierarchical"] is True
        # Unrelated config preserved by the retrieve-merge-write.
        assert body["CascadeConfiguration"] == _FULL_ONE_TO_MANY["CascadeConfiguration"]

    def test_put_body_carries_discriminator_when_cast_get_omits_it(self, backend):
        # Under minimal OData metadata a GET to the cast path omits @odata.type
        # (it is inferable from the cast context), but the un-cast PUT target is
        # a polymorphic set that needs the discriminator to know which derived
        # type to replace. The fix injects it from the resolved cast so the PUT
        # body is never missing it.
        from crm.core import metadata_update as mu
        resolve = backend.url_for(
            "RelationshipDefinitions(SchemaName='new_account_new_project')"
        )
        cast = backend.url_for(
            f"RelationshipDefinitions({_REL_ID})"
            "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        )
        uncast = backend.url_for(f"RelationshipDefinitions({_REL_ID})")
        cast_get_no_type = {
            k: v for k, v in _FULL_ONE_TO_MANY.items() if k != "@odata.type"
        }
        with requests_mock.Mocker() as m:
            m.get(resolve, json={
                "@odata.type": "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
                "MetadataId": _REL_ID,
                "SchemaName": "new_account_new_project",
                "RelationshipType": "OneToManyRelationship",
            })
            m.get(cast, json=cast_get_no_type)
            m.put(uncast, status_code=204)
            mu.update_relationship(
                backend, "new_account_new_project",
                cascade={"Delete": "Restrict"},
            )
        body = m.request_history[-1].json()
        assert body["@odata.type"] == \
            "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        # The change still landed and siblings survived.
        assert body["CascadeConfiguration"]["Delete"] == "Restrict"
        assert body["CascadeConfiguration"]["Assign"] == "NoCascade"


def _mock_resolve_n_n(m, backend):
    """Register the N:N relationship resolve GET. Returns the resolve URL."""
    resolve = backend.url_for(
        "RelationshipDefinitions(SchemaName='new_account_new_project_n_n')"
    )
    m.get(resolve, json={
        "@odata.type": "#Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
        "MetadataId": _REL_ID,
        "SchemaName": "new_account_new_project_n_n",
        "RelationshipType": "ManyToManyRelationship",
    })
    return resolve


class TestUpdateRelationshipManyToManyGuards:
    def test_cascade_on_n_n_raises_client_side_no_put(self, backend):
        from crm.core import metadata_update as mu
        with requests_mock.Mocker() as m:
            _mock_resolve_n_n(m, backend)
            put = m.put(requests_mock.ANY, status_code=204)
            with pytest.raises(D365Error, match="one-to-many"):
                mu.update_relationship(
                    backend, "new_account_new_project_n_n",
                    cascade={"Delete": "Restrict"},
                )
            assert put.call_count == 0

    def test_menu_on_n_n_raises_client_side_no_put(self, backend):
        from crm.core import metadata_update as mu
        with requests_mock.Mocker() as m:
            _mock_resolve_n_n(m, backend)
            put = m.put(requests_mock.ANY, status_code=204)
            with pytest.raises(D365Error, match="one-to-many"):
                mu.update_relationship(
                    backend, "new_account_new_project_n_n",
                    menu_behavior="UseLabel",
                )
            assert put.call_count == 0

    def test_hierarchical_on_n_n_raises_client_side_no_put(self, backend):
        from crm.core import metadata_update as mu
        with requests_mock.Mocker() as m:
            _mock_resolve_n_n(m, backend)
            put = m.put(requests_mock.ANY, status_code=204)
            with pytest.raises(D365Error, match="one-to-many"):
                mu.update_relationship(
                    backend, "new_account_new_project_n_n",
                    is_hierarchical=True,
                )
            assert put.call_count == 0


class TestUpdateRelationshipCascadeKey:
    def test_bad_cascade_key_raises_client_side_no_put(self, backend):
        from crm.core import metadata_update as mu
        with requests_mock.Mocker() as m:
            put = m.put(requests_mock.ANY, status_code=204)
            with pytest.raises(D365Error, match="cascade"):
                mu.update_relationship(
                    backend, "new_account_new_project",
                    cascade={"Delte": "Restrict"},
                )
            # Bad key is caught before resolving/PUT.
            assert put.call_count == 0


_FULL_DECIMAL_ATTR = {
    "@odata.type": "#Microsoft.Dynamics.CRM.DecimalAttributeMetadata",
    "MetadataId": "77777777-7777-7777-7777-777777777777",
    "SchemaName": "new_Amount",
    "LogicalName": "new_amount",
    "Precision": 2,
    "DisplayName": {"LocalizedLabels": [{"Label": "Amount", "LanguageCode": 1033}]},
    "RequiredLevel": {"Value": "None", "CanBeChanged": True},
}

_FULL_MONEY_ATTR = {
    "@odata.type": "#Microsoft.Dynamics.CRM.MoneyAttributeMetadata",
    "MetadataId": "88888888-8888-8888-8888-888888888888",
    "SchemaName": "new_Cost",
    "LogicalName": "new_cost",
    "Precision": 2,
    "DisplayName": {"LocalizedLabels": [{"Label": "Cost", "LanguageCode": 1033}]},
    "RequiredLevel": {"Value": "None", "CanBeChanged": True},
}


class TestUpdateAttributePrecisionRange:
    def test_decimal_precision_over_max_raises_client_side_no_put(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_amount')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.DecimalAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_DECIMAL_ATTR)
            put = m.put(cast, status_code=204)
            with pytest.raises(D365Error, match="precision"):
                mu.update_attribute(backend, "new_project", "new_amount", precision=11)
            assert put.call_count == 0

    def test_money_precision_over_max_raises_client_side_no_put(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_cost')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.MoneyAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_MONEY_ATTR)
            put = m.put(cast, status_code=204)
            with pytest.raises(D365Error, match="precision"):
                mu.update_attribute(backend, "new_project", "new_cost", precision=5)
            assert put.call_count == 0

    def test_decimal_precision_in_range_puts(self, backend):
        from crm.core import metadata_update as mu
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_amount')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.DecimalAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_FULL_DECIMAL_ATTR)
            m.get(cast, json=_FULL_DECIMAL_ATTR)
            m.put(cast, status_code=204)
            mu.update_attribute(backend, "new_project", "new_amount", precision=4)
        body = m.request_history[-1].json()
        assert body["Precision"] == 4


class TestDryRun:
    def test_dry_run_does_not_put_and_returns_merged_body_and_diff(self, dry_backend):
        from crm.core import metadata_update as mu
        path = dry_backend.url_for("EntityDefinitions(LogicalName='new_project')")
        with requests_mock.Mocker() as m:
            getter = m.get(path, json=_FULL_ENTITY)
            put = m.put(path, status_code=204)
            out = mu.update_entity(dry_backend, "new_project",
                                   display_name="Engagement")
            assert getter.call_count == 1
            assert put.call_count == 0
        assert out["_dry_run"] is True
        assert out["method"] == "PUT"
        # Merged body has the new value AND preserves originals.
        assert out["body"]["DisplayName"]["LocalizedLabels"][0]["Label"] == "Engagement"
        assert out["body"]["OwnershipType"] == "UserOwned"
        # Diff: changed key old -> new.
        assert "DisplayName" in out["diff"]
        assert out["diff"]["DisplayName"]["old"] == _FULL_ENTITY["DisplayName"]
        assert out["diff"]["DisplayName"]["new"]["LocalizedLabels"][0]["Label"] == \
            "Engagement"

    def test_dry_run_attribute_gets_no_put_returns_merged_body_and_diff(
        self, dry_backend
    ):
        from crm.core import metadata_update as mu
        base_rel = (
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_code')"
        )
        cast_rel = base_rel + "/Microsoft.Dynamics.CRM.StringAttributeMetadata"
        base = dry_backend.url_for(base_rel)
        cast = dry_backend.url_for(cast_rel)
        with requests_mock.Mocker() as m:
            base_get = m.get(base, json=_FULL_STRING_ATTR)
            cast_get = m.get(cast, json=_FULL_STRING_ATTR)
            put = m.put(cast, status_code=204)
            out = mu.update_attribute(dry_backend, "new_project", "new_code",
                                      required="ApplicationRequired")
            # The un-cast base GET fires to learn @odata.type; the cast GET then
            # fetches the full typed definition used as the merge base. No PUT is
            # sent in dry-run.
            assert base_get.call_count == 1
            assert cast_get.call_count == 1
            assert put.call_count == 0
        assert out["_dry_run"] is True
        assert out["method"] == "PUT"
        assert out["path"] == cast_rel
        # Merged body: change landed, other props preserved.
        assert out["body"]["RequiredLevel"]["Value"] == "ApplicationRequired"
        assert out["body"]["MaxLength"] == 100
        assert out["body"]["FormatName"] == {"Value": "Text"}
        # Diff: changed key old -> new.
        assert out["diff"]["RequiredLevel"]["old"] == \
            _FULL_STRING_ATTR["RequiredLevel"]
        assert out["diff"]["RequiredLevel"]["new"]["Value"] == "ApplicationRequired"

    def test_dry_run_relationship_resolves_id_gets_no_put(self, dry_backend):
        from crm.core import metadata_update as mu
        resolve = dry_backend.url_for(
            "RelationshipDefinitions(SchemaName='new_account_new_project')"
        )
        cast_rel = (
            f"RelationshipDefinitions({_REL_ID})"
            "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        )
        uncast_rel = f"RelationshipDefinitions({_REL_ID})"
        cast = dry_backend.url_for(cast_rel)
        uncast = dry_backend.url_for(uncast_rel)
        with requests_mock.Mocker() as m:
            resolve_get = m.get(resolve, json={
                "@odata.type": "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
                "MetadataId": _REL_ID,
                "SchemaName": "new_account_new_project",
                "RelationshipType": "OneToManyRelationship",
            })
            cast_get = m.get(cast, json=_FULL_ONE_TO_MANY)
            put = m.put(uncast, status_code=204)
            out = mu.update_relationship(
                dry_backend, "new_account_new_project",
                cascade={"Delete": "Restrict"},
            )
            # MetadataId is resolved first, then the cast definition is fetched
            # for the merge; no PUT is sent in dry-run.
            assert resolve_get.call_count == 1
            assert cast_get.call_count == 1
            assert put.call_count == 0
        assert out["_dry_run"] is True
        assert out["method"] == "PUT"
        # Dry-run preview reflects the un-cast PUT target, not the cast read path.
        assert out["path"] == uncast_rel
        assert out["body"]["@odata.type"] == \
            "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
        # Merged body: changed cascade member + preserved siblings.
        cc = out["body"]["CascadeConfiguration"]
        assert cc["Delete"] == "Restrict"
        assert cc["Assign"] == "NoCascade"
        # Diff: CascadeConfiguration old -> new.
        assert out["diff"]["CascadeConfiguration"]["new"]["Delete"] == "Restrict"


class TestPublishGating:
    def test_publish_true_posts_publishallxml(self, backend):
        from crm.core import metadata_update as mu
        path = backend.url_for("EntityDefinitions(LogicalName='new_project')")
        pub = backend.url_for("PublishAllXml")
        with requests_mock.Mocker() as m:
            m.get(path, json=_FULL_ENTITY)
            m.put(path, status_code=204)
            publish_mock = m.post(pub, status_code=204)
            out = mu.update_entity(backend, "new_project",
                                   display_name="X", publish=True)
        assert publish_mock.call_count == 1
        assert out["published"] is True

    def test_publish_false_does_not_post_publishallxml(self, backend):
        from crm.core import metadata_update as mu
        path = backend.url_for("EntityDefinitions(LogicalName='new_project')")
        pub = backend.url_for("PublishAllXml")
        with requests_mock.Mocker() as m:
            m.get(path, json=_FULL_ENTITY)
            m.put(path, status_code=204)
            publish_mock = m.post(pub, status_code=204)
            mu.update_entity(backend, "new_project",
                             display_name="X", publish=False)
        assert publish_mock.call_count == 0
