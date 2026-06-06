"""Unit tests for `build_entity_spec` — live entity -> apply-spec projection (#92).

Mirrors `test_metadata_describe.py`: a real `D365Backend` driven by
`requests_mock`, asserting the exact GET paths via `url_for`. Each test mocks
ONLY the round-trips its scenario makes (requests_mock raises NoMockAddress on
any unregistered endpoint), so over-fetching surfaces as a test failure.

The load-bearing assertion is the round-trip: `apply.validate_spec` must accept
whatever `build_entity_spec` produces.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.core import apply
from crm.core.export_spec import build_entity_spec
from crm.utils.d365_backend import ConnectionProfile, D365Backend


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


# ── URL helpers ──────────────────────────────────────────────────────────────

def _entity_url(backend, entity="new_project") -> str:
    return backend.url_for(f"EntityDefinitions(LogicalName='{entity}')")


def _attrs_url(backend, entity="new_project") -> str:
    return backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes")


def _attr_url(backend, attr, entity="new_project") -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{attr}')"
    )


def _pick_cast_url(backend, attr, entity="new_project") -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{attr}')"
        "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
    )


def _o2m_url(backend, entity="new_project") -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/OneToManyRelationships"
    )


# ── fixture builders ─────────────────────────────────────────────────────────

def _label(text):
    return {"UserLocalizedLabel": {"Label": text, "LanguageCode": 1033}}


def _opt(value, lbl):
    return {"Value": value, "Label": _label(lbl)}


def _shallow(logical, *, custom=True):
    return {"LogicalName": logical, "SchemaName": logical, "IsCustomAttribute": custom}


_ENTITY = {
    "LogicalName": "new_project",
    "SchemaName": "new_Project",
    "DisplayName": _label("Project"),
    "DisplayCollectionName": _label("Projects"),
    "OwnershipType": "UserOwned",
    "PrimaryNameAttribute": "new_name",
}


def _primary_info():
    return {
        "SchemaName": "new_Name",
        "DisplayName": _label("Project Name"),
        "AttributeTypeName": {"Value": "StringType"},
        "RequiredLevel": {"Value": "ApplicationRequired"},
        "MaxLength": 200,
        "FormatName": {"Value": "Text"},
    }


def _string_info():
    return {
        "SchemaName": "new_Code",
        "DisplayName": _label("Code"),
        "Description": _label("Project code"),
        "AttributeTypeName": {"Value": "StringType"},
        "RequiredLevel": {"Value": "Recommended"},
        "MaxLength": 50,
        "FormatName": {"Value": "Text"},
    }


def _decimal_info():
    return {
        "SchemaName": "new_Budget",
        "DisplayName": _label("Budget"),
        "AttributeTypeName": {"Value": "DecimalType"},
        "RequiredLevel": {"Value": "None"},
        "Precision": 2,
    }


def _lookup_info():
    return {
        "SchemaName": "new_AccountId",
        "DisplayName": _label("Account"),
        "AttributeTypeName": {"Value": "LookupType"},
        "RequiredLevel": {"Value": "None"},
        "Targets": ["account"],
    }


def _local_pick_info():
    return {
        "SchemaName": "new_Stage",
        "DisplayName": _label("Stage"),
        "AttributeTypeName": {"Value": "PicklistType"},
        "RequiredLevel": {"Value": "None"},
    }


def _global_pick_info(schema):
    return {
        "SchemaName": schema,
        "DisplayName": _label(schema),
        "AttributeTypeName": {"Value": "PicklistType"},
        "RequiredLevel": {"Value": "None"},
    }


def _owner_info():
    # System attribute apply cannot create — must be skipped.
    return {
        "SchemaName": "OwnerId",
        "DisplayName": _label("Owner"),
        "AttributeTypeName": {"Value": "OwnerType"},
        "RequiredLevel": {"Value": "SystemRequired"},
    }


# ── tests ────────────────────────────────────────────────────────────────────

class TestEntityLevel:
    def test_entity_primary_attr_and_ownership(self, backend):
        attrs = {"value": [_shallow("new_name")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            spec = build_entity_spec(backend, "new_project")

        ent = spec["entities"][0]
        assert ent["schema_name"] == "new_Project"
        assert ent["display_name"] == "Project"
        assert ent["display_collection_name"] == "Projects"
        assert ent["ownership"] == "UserOwned"
        assert ent["primary_attr"] == {"schema_name": "new_Name", "label": "Project Name"}
        # Primary name attribute is NOT re-created as a column.
        assert "attributes" not in ent
        # No global option sets referenced.
        assert "optionsets" not in spec


class TestStringAttribute:
    def test_string_max_length_format_required(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_code")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=_string_info())
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col == {
            "kind": "string",
            "schema_name": "new_Code",
            "display_name": "Code",
            "description": "Project code",
            "required": "Recommended",
            "max_length": 50,
            "format_name": "Text",
        }


class TestNumericAttribute:
    def test_decimal_precision(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_budget")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_budget"), json=_decimal_info())
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["kind"] == "decimal"
        assert col["precision"] == 2
        assert col["required"] == "None"
        assert "max_length" not in col
        assert "format_name" not in col


class TestLookupAttribute:
    def test_lookup_target_entity(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_accountid")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_accountid"), json=_lookup_info())
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["kind"] == "lookup"
        assert col["target_entity"] == "account"


class TestLocalPicklist:
    def test_local_picklist_inline_options_no_optionsets(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_stage")]}
        cast = {
            "LogicalName": "new_stage",
            "OptionSet": {"Options": [_opt(1, "New"), _opt(2, "Done")]},
            "GlobalOptionSet": None,
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_stage"), json=_local_pick_info())
            m.get(_pick_cast_url(backend, "new_stage"), json=cast)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["kind"] == "picklist"
        assert col["options"] == [
            {"value": 1, "label": "New"},
            {"value": 2, "label": "Done"},
        ]
        assert "optionset_name" not in col
        # A LOCAL option set adds NO top-level optionsets accumulator entry.
        assert "optionsets" not in spec


class TestGlobalPicklist:
    def test_global_picklist_emits_optionset_name_and_dedups(self, backend):
        # Two attributes share ONE global option set -> exactly one optionsets entry.
        attrs = {"value": [
            _shallow("new_name"),
            _shallow("new_priority"),
            _shallow("new_priority2"),
        ]}
        glob = {"Name": "new_priorityset", "IsGlobal": True}
        cast_a = {
            "LogicalName": "new_priority",
            "OptionSet": None,
            "GlobalOptionSet": glob,
        }
        cast_b = {
            "LogicalName": "new_priority2",
            "OptionSet": None,
            "GlobalOptionSet": glob,
        }
        gos = {
            "Name": "new_priorityset",
            "DisplayName": _label("Priority"),
            "Options": [_opt(10, "Low"), _opt(20, "High")],
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_priority"),
                  json=_global_pick_info("new_Priority"))
            m.get(_attr_url(backend, "new_priority2"),
                  json=_global_pick_info("new_Priority2"))
            m.get(_pick_cast_url(backend, "new_priority"), json=cast_a)
            m.get(_pick_cast_url(backend, "new_priority2"), json=cast_b)
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priorityset')"),
                json=gos,
            )
            spec = build_entity_spec(backend, "new_project")

        cols = spec["entities"][0]["attributes"]
        assert all(c["optionset_name"] == "new_priorityset" for c in cols)
        assert all("options" not in c for c in cols)
        # Deduped: exactly ONE optionsets entry despite two referencing attrs.
        assert len(spec["optionsets"]) == 1
        os_entry = spec["optionsets"][0]
        assert os_entry["name"] == "new_priorityset"
        assert os_entry["display_name"] == "Priority"
        assert os_entry["options"] == [
            {"value": 10, "label": "Low"},
            {"value": 20, "label": "High"},
        ]


class TestSystemAttributesExcluded:
    def test_primary_and_system_attrs_excluded(self, backend):
        attrs = {"value": [
            _shallow("new_name"),            # primary name -> primary_attr only
            _shallow("ownerid", custom=False),  # non-custom system -> skipped
            _shallow("new_owner"),           # custom but OwnerType kind -> skipped
            _shallow("new_code"),            # the only real column
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_owner"), json=_owner_info())
            m.get(_attr_url(backend, "new_code"), json=_string_info())
            spec = build_entity_spec(backend, "new_project")

        cols = spec["entities"][0]["attributes"]
        # ownerid is non-custom (never deep-read); new_owner is OwnerType (skipped).
        assert [c["schema_name"] for c in cols] == ["new_Code"]


class TestOptInViewsAndRelationships:
    def test_both_false_omits_keys(self, backend):
        attrs = {"value": [_shallow("new_name")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            spec = build_entity_spec(backend, "new_project")
        ent = spec["entities"][0]
        assert "views" not in ent
        assert "relationships" not in ent

    def test_with_views_includes_views(self, backend):
        from crm.core.views import _build_layoutxml, _build_fetchxml
        attrs = {"value": [_shallow("new_name")]}
        cols = [("new_name", 200)]
        layoutxml = _build_layoutxml("new_project", 10042, cols)
        fetchxml = _build_fetchxml("new_project", cols, "new_name", False)
        savedqueries = {"value": [{
            "name": "Active Projects",
            "layoutxml": layoutxml,
            "fetchxml": fetchxml,
            "isdefault": True,
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(backend.url_for("savedqueries"), json=savedqueries)
            spec = build_entity_spec(backend, "new_project", with_views=True)

        views = spec["entities"][0]["views"]
        assert len(views) == 1
        assert views[0]["name"] == "Active Projects"
        assert views[0]["is_default"] is True
        assert {c["name"] for c in views[0]["columns"]} == {"new_name"}

    def test_with_relationships_includes_relationships(self, backend):
        attrs = {"value": [_shallow("new_name")]}
        o2m = {"value": [{
            "SchemaName": "new_project_new_task",
            "ReferencedEntity": "new_project",
            "ReferencingEntity": "new_task",
            "ReferencingAttribute": "new_projectid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {"Assign": "NoCascade", "Delete": "RemoveLink"},
            "AssociatedMenuConfiguration": {"Behavior": "UseCollectionName"},
        }]}
        rel_attr = {
            "LogicalName": "new_projectid",
            "DisplayName": _label("Project"),
            "RequiredLevel": {"Value": "None"},
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_o2m_url(backend), json=o2m)
            m.get(_attr_url(backend, "new_projectid", entity="new_task"), json=rel_attr)
            spec = build_entity_spec(backend, "new_project", with_relationships=True)

        rels = spec["entities"][0]["relationships"]
        assert len(rels) == 1
        assert rels[0]["schema_name"] == "new_project_new_task"
        assert rels[0]["lookup_schema"] == "new_projectid"


class TestRoundTrip:
    def test_validate_spec_accepts_full_projection(self, backend):
        """The load-bearing contract: a projected spec with attributes +
        relationships + views + a global option set MUST pass validate_spec."""
        attrs = {"value": [
            _shallow("new_name"),
            _shallow("new_code"),
            _shallow("new_budget"),
            _shallow("new_accountid"),
            _shallow("new_stage"),
            _shallow("new_priority"),
        ]}
        local_cast = {
            "LogicalName": "new_stage",
            "OptionSet": {"Options": [_opt(1, "New")]},
            "GlobalOptionSet": None,
        }
        global_cast = {
            "LogicalName": "new_priority",
            "OptionSet": None,
            "GlobalOptionSet": {"Name": "new_priorityset", "IsGlobal": True},
        }
        gos = {
            "Name": "new_priorityset",
            "DisplayName": _label("Priority"),
            "Options": [_opt(10, "Low")],
        }
        from crm.core.views import _build_layoutxml, _build_fetchxml
        cols = [("new_name", 200)]
        savedqueries = {"value": [{
            "name": "All",
            "layoutxml": _build_layoutxml("new_project", 10042, cols),
            "fetchxml": _build_fetchxml("new_project", cols, None, False),
            "isdefault": False,
        }]}
        o2m = {"value": [{
            "SchemaName": "new_project_new_task",
            "ReferencedEntity": "new_project",
            "ReferencingEntity": "new_task",
            "ReferencingAttribute": "new_projectid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {},
            "AssociatedMenuConfiguration": {},
        }]}
        rel_attr = {
            "LogicalName": "new_projectid",
            "DisplayName": _label("Project"),
            "RequiredLevel": {"Value": "None"},
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=_string_info())
            m.get(_attr_url(backend, "new_budget"), json=_decimal_info())
            m.get(_attr_url(backend, "new_accountid"), json=_lookup_info())
            m.get(_attr_url(backend, "new_stage"), json=_local_pick_info())
            m.get(_attr_url(backend, "new_priority"),
                  json=_global_pick_info("new_Priority"))
            m.get(_pick_cast_url(backend, "new_stage"), json=local_cast)
            m.get(_pick_cast_url(backend, "new_priority"), json=global_cast)
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priorityset')"),
                json=gos,
            )
            m.get(backend.url_for("savedqueries"), json=savedqueries)
            m.get(_o2m_url(backend), json=o2m)
            m.get(_attr_url(backend, "new_projectid", entity="new_task"), json=rel_attr)
            spec = build_entity_spec(
                backend, "new_project", with_views=True, with_relationships=True
            )
            # Pure read-only projection — every round-trip is a GET.
            assert {r.method for r in m.request_history} == {"GET"}

        # The contract: validate_spec must not raise on the projected spec.
        apply.validate_spec(spec)

        ent = spec["entities"][0]
        kinds = {c["schema_name"]: c["kind"] for c in ent["attributes"]}
        assert kinds == {
            "new_Code": "string",
            "new_Budget": "decimal",
            "new_AccountId": "lookup",
            "new_Stage": "picklist",
            "new_Priority": "picklist",
        }
        assert ent["primary_attr"]["schema_name"] == "new_Name"
        assert len(spec["optionsets"]) == 1
