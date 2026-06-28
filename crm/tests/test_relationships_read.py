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

        # _FULL_ROW's cascade is entirely at create_one_to_many defaults, so no
        # cascade_* keys are emitted (defaults omitted; RollupView is not in the
        # relationship adapter and is dropped regardless).
        assert not any(k.startswith("cascade") for k in r)
        # Menu: UseLabel behavior + its label emit; Order 10000 is the default
        # (omitted) and Group is not an adapter key (dropped).
        assert r["menu_behavior"] == "UseLabel"
        assert r["menu_label"] == "Projects"
        assert "menu_order" not in r
        assert "menu_group" not in r and "group" not in r

    def test_non_default_cascade_and_menu_emit_flat_adapter_keys(self, backend):
        """Non-default cascade/menu emit the FLAT keys the relationship adapter
        consumes (cascade_*, menu_behavior/menu_label/menu_order, is_hierarchical)
        — not a nested `cascade`/`associated_menu` object the adapter ignores.
        Dimensions at their create_one_to_many default are omitted (no bloat)."""
        from crm.core import relationships as rel
        row = {
            "SchemaName": "new_account_new_project",
            "ReferencedEntity": "account",
            "ReferencingEntity": "new_project",
            "ReferencingAttribute": "new_accountid",
            "IsCustomRelationship": True,
            "IsHierarchical": True,
            "CascadeConfiguration": {
                "@odata.type": "#Microsoft.Dynamics.CRM.CascadeConfiguration",
                "Assign": "Cascade",          # non-default → emitted
                "Delete": "RemoveLink",       # default → omitted
                "Reparent": "NoCascade",      # default → omitted
                "Share": "Cascade",           # non-default → emitted
                "Unshare": "NoCascade",       # default → omitted
                "Merge": "NoCascade",         # default → omitted
                "RollupView": "Cascade",      # not in adapter → dropped
            },
            "AssociatedMenuConfiguration": {
                "Behavior": "UseLabel",
                "Group": "Details",           # not in adapter → dropped
                "Order": 500,                 # non-default → emitted
                "Label": {
                    "UserLocalizedLabel": {"Label": "Projects", "LanguageCode": 1033},
                },
            },
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [row]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=_ATTR_INFO)
            result = rel.read_entity_relationships(backend, "new_project")

        r = result[0]
        assert "cascade" not in r and "associated_menu" not in r
        assert r["cascade_assign"] == "Cascade"
        assert r["cascade_share"] == "Cascade"
        # Defaults and non-adapter dimensions omitted.
        for omitted in ("cascade_delete", "cascade_reparent", "cascade_unshare",
                        "cascade_merge", "cascade_rollup_view", "rollup_view"):
            assert omitted not in r
        assert r["menu_behavior"] == "UseLabel"
        assert r["menu_label"] == "Projects"
        assert r["menu_order"] == 500
        assert "group" not in r and "menu_group" not in r
        assert r["is_hierarchical"] is True

    def test_uselabel_without_readable_label_omits_menu_behavior(self, backend):
        """A UseLabel menu whose Label is missing/empty must NOT emit
        menu_behavior alone — validate_spec rejects UseLabel without menu_label,
        which would make export-spec output un-appliable. Fall back to omitting
        the menu keys (default behavior)."""
        from crm.core import relationships as rel
        row = {
            "SchemaName": "new_account_new_project",
            "ReferencedEntity": "account",
            "ReferencingEntity": "new_project",
            "ReferencingAttribute": "new_accountid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {},
            "AssociatedMenuConfiguration": {
                "Behavior": "UseLabel",
                "Label": {"UserLocalizedLabel": None, "LocalizedLabels": []},
                "Order": 500,
            },
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [row]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=_ATTR_INFO)
            result = rel.read_entity_relationships(backend, "new_project")
        r = result[0]
        assert "menu_behavior" not in r
        assert "menu_label" not in r
        # A non-default Order is still independent of the behavior fallback.
        assert r["menu_order"] == 500

    def test_default_cascade_and_menu_emit_nothing(self, backend):
        """A relationship at all platform defaults emits no cascade_*/menu_* keys."""
        from crm.core import relationships as rel
        row = {
            "SchemaName": "new_account_new_project",
            "ReferencedEntity": "account",
            "ReferencingEntity": "new_project",
            "ReferencingAttribute": "new_accountid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {
                "Assign": "NoCascade", "Delete": "RemoveLink", "Reparent": "NoCascade",
                "Share": "NoCascade", "Unshare": "NoCascade", "Merge": "NoCascade",
            },
            "AssociatedMenuConfiguration": {"Behavior": "UseCollectionName", "Order": 10000},
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [row]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=_ATTR_INFO)
            result = rel.read_entity_relationships(backend, "new_project")

        r = result[0]
        assert not any(k.startswith("cascade") or k.startswith("menu") for k in r)
        assert "is_hierarchical" not in r

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

    def test_lookup_description_emitted_when_present(self, backend):
        """The lookup column's Description rides the attribute read the projection
        already makes, so it is emitted as `lookup_description` (an adapter key);
        omitted when blank."""
        from crm.core import relationships as rel
        attr_with_desc = {
            "LogicalName": "new_accountid",
            "DisplayName": {"UserLocalizedLabel": {"Label": "Account", "LanguageCode": 1033}},
            "Description": {"UserLocalizedLabel": {"Label": "The owning account", "LanguageCode": 1033}},
            "RequiredLevel": {"Value": "None"},
        }
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [_FULL_ROW]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=attr_with_desc)
            result = rel.read_entity_relationships(backend, "new_project")
        assert result[0]["lookup_description"] == "The owning account"

    def test_lookup_description_omitted_when_blank(self, backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(_o2m_url(backend), json={"value": [_FULL_ROW]})
            m.get(_attr_url(backend, "new_project", "new_accountid"), json=_ATTR_INFO)
            result = rel.read_entity_relationships(backend, "new_project")
        assert "lookup_description" not in result[0]

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
        # Both Assign and Delete are non-default (Cascade) → flat keys emitted.
        assert r2["cascade_assign"] == "Cascade"
        assert r2["cascade_delete"] == "Cascade"
        assert r2["required"] == "ApplicationRequired"
        # Menu: UseCollectionName is the default behavior (omitted); Order 500 is
        # non-default (emitted); no label for a non-UseLabel behavior.
        assert "menu_behavior" not in r2
        assert r2["menu_order"] == 500
        assert "menu_label" not in r2

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
