"""Unit tests for read_entity_relationships in crm.core.relationships."""
# pyright: basic

from __future__ import annotations

import requests_mock



def _o2m_url(backend, entity: str = "new_project") -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/OneToManyRelationships"
    )


def _attr_url(backend, entity: str, attr: str) -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{attr}')"
    )


_FULL_ROW = {
    "SchemaName": "new_account_new_project",
    "ReferencedEntity": "account",
    "ReferencingEntity": "new_project",
    "ReferencingAttribute": "new_accountid",
    "IsCustomRelationship": True,
    "CascadeConfiguration": {
        "@odata.type": "#Microsoft.Dynamics.CRM.CascadeConfiguration",
        "Assign": "NoCascade",
        "Delete": "RemoveLink",
        "Reparent": "NoCascade",
        "Share": "NoCascade",
        "Unshare": "NoCascade",
        "Merge": "NoCascade",
        "RollupView": "NoCascade",
    },
    "AssociatedMenuConfiguration": {
        "@odata.type": "#Microsoft.Dynamics.CRM.AssociatedMenuConfiguration",
        "Behavior": "UseLabel",
        "Group": "Details",
        "Order": 10000,
        "Label": {
            "LocalizedLabels": [{"Label": "Projects", "LanguageCode": 1033}],
            "UserLocalizedLabel": {"Label": "Projects", "LanguageCode": 1033},
        },
    },
}

_ATTR_INFO = {
    "LogicalName": "new_accountid",
    "DisplayName": {
        "UserLocalizedLabel": {"Label": "Account", "LanguageCode": 1033},
        "LocalizedLabels": [{"Label": "Account", "LanguageCode": 1033}],
    },
    "RequiredLevel": {"Value": "None"},
}


class TestReadEntityRelationshipsFull:
    def test_happy_path_full_cascade_and_menu(self, backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [_FULL_ROW]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=_ATTR_INFO)
            result = rel.read_entity_relationships(backend, "new_project")

        assert len(result) == 1
        r = result[0]

        # Core apply-spec fields
        assert r["schema_name"] == "new_account_new_project"
        assert r["referenced_entity"] == "account"
        assert r["referencing_entity"] == "new_project"
        assert r["lookup_schema"] == "new_accountid"
        assert r["lookup_display"] == "Account"
        assert r["required"] == "None"

        # Cascade: @-annotations stripped, keys snake_cased, values verbatim
        cascade = r["cascade"]
        assert "@odata.type" not in cascade
        assert cascade["assign"] == "NoCascade"
        assert cascade["delete"] == "RemoveLink"
        assert cascade["reparent"] == "NoCascade"
        assert cascade["rollup_view"] == "NoCascade"

        # AssociatedMenuConfiguration: @-stripped, snake_case, label extracted
        menu = r["associated_menu"]
        assert "@odata.type" not in menu
        assert menu["behavior"] == "UseLabel"
        assert menu["group"] == "Details"
        assert menu["order"] == 10000
        assert menu["label"] == "Projects"

    def test_system_relationship_excluded(self, backend):
        from crm.core import relationships as rel
        system_row = {
            "SchemaName": "account_contacts",
            "ReferencedEntity": "account",
            "ReferencingEntity": "contact",
            "ReferencingAttribute": "parentcustomerid",
            "IsCustomRelationship": False,
            "CascadeConfiguration": {},
            "AssociatedMenuConfiguration": {},
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [_FULL_ROW, system_row]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=_ATTR_INFO)
            result = rel.read_entity_relationships(backend, "new_project")

        # Only the custom relationship is returned
        assert len(result) == 1
        assert result[0]["schema_name"] == "new_account_new_project"

    def test_lookup_display_falls_back_to_referencing_attr_when_no_label(self, backend):
        from crm.core import relationships as rel
        attr_no_label = {
            "LogicalName": "new_accountid",
            "DisplayName": {
                "UserLocalizedLabel": None,
                "LocalizedLabels": [],
            },
            "RequiredLevel": {"Value": "Recommended"},
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [_FULL_ROW]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=attr_no_label)
            result = rel.read_entity_relationships(backend, "new_project")

        assert len(result) == 1
        # No label → fall back to the logical name of the referencing attribute
        assert result[0]["lookup_display"] == "new_accountid"
        # Required level still captured
        assert result[0]["required"] == "Recommended"

    def test_no_custom_relationships_returns_empty_list(self, backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": []})
            result = rel.read_entity_relationships(backend, "new_project")

        assert result == []

    def test_single_quote_in_entity_name_is_escaped_in_url(self, backend):
        from crm.core import relationships as rel
        # Entity name with a single quote — must be escaped as '' in the URL
        entity_with_quote = "it's_table"
        escaped_url = backend.url_for(
            "EntityDefinitions(LogicalName='it''s_table')/OneToManyRelationships"
        )
        with requests_mock.Mocker() as m:
            m.get(escaped_url, json={"value": []})
            result = rel.read_entity_relationships(backend, entity_with_quote)

        assert result == []
        assert m.called
        called_url = m.last_request.url
        assert "it''s_table" in called_url

    def test_all_custom_returns_both_filtered(self, backend):
        """When multiple custom 1:N exist, all are returned."""
        from crm.core import relationships as rel
        row2 = {
            "SchemaName": "new_contact_new_project",
            "ReferencedEntity": "contact",
            "ReferencingEntity": "new_project",
            "ReferencingAttribute": "new_contactid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {"Assign": "Cascade", "Delete": "Cascade"},
            "AssociatedMenuConfiguration": {"Behavior": "UseCollectionName", "Group": "Sales", "Order": 500},
        }
        attr2 = {
            "LogicalName": "new_contactid",
            "DisplayName": {"UserLocalizedLabel": {"Label": "Contact"}},
            "RequiredLevel": {"Value": "ApplicationRequired"},
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [_FULL_ROW, row2]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=_ATTR_INFO)
            m.get(_attr_url(backend, "new_project", "new_contactid"), json=attr2)
            result = rel.read_entity_relationships(backend, "new_project")

        assert len(result) == 2
        names = {r["schema_name"] for r in result}
        assert names == {"new_account_new_project", "new_contact_new_project"}
        r2 = next(r for r in result if r["schema_name"] == "new_contact_new_project")
        assert r2["cascade"]["assign"] == "Cascade"
        assert r2["required"] == "ApplicationRequired"
        # row2's menu has no Label key — projection should omit "label" key
        menu2 = r2["associated_menu"]
        assert menu2["behavior"] == "UseCollectionName"
        assert menu2["group"] == "Sales"
        assert menu2["order"] == 500
        assert "label" not in menu2

    def test_attribute_info_404_falls_back_gracefully(self, backend):
        """When attribute_info raises D365Error (404), lookup_display falls back
        to the referencing attribute logical name and 'required' is omitted."""
        from crm.core import relationships as rel

        custom_row = {
            "SchemaName": "new_account_new_project",
            "ReferencedEntity": "account",
            "ReferencingEntity": "new_project",
            "ReferencingAttribute": "new_accountid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {},
            "AssociatedMenuConfiguration": {},
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [custom_row]})
            m.get(
                _attr_url(backend, "new_project", "new_accountid"),
                status_code=404,
                json={"error": {"code": "0x80040217", "message": "Could not find attribute"}},
            )
            result = rel.read_entity_relationships(backend, "new_project")

        assert len(result) == 1
        r = result[0]
        assert r["lookup_display"] == "new_accountid"
        assert "required" not in r
