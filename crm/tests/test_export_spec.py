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

import requests_mock

from crm.core import apply
from crm.core.export_spec import build_entity_spec


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


def _multi_cast_url(backend, attr, entity="new_project") -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{attr}')"
        "/Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata"
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
        "AttributeType": "Picklist",
        "AttributeTypeName": {"Value": "PicklistType"},
        "RequiredLevel": {"Value": "None"},
    }


def _global_pick_info(schema):
    return {
        "SchemaName": schema,
        "DisplayName": _label(schema),
        "AttributeType": "Picklist",
        "AttributeTypeName": {"Value": "PicklistType"},
        "RequiredLevel": {"Value": "None"},
    }


def _multiselect_info(schema="new_Tags"):
    return {
        "SchemaName": schema,
        "DisplayName": _label("Tags"),
        "AttributeTypeName": {"Value": "MultiSelectPicklistType"},
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


    def test_string_uncreatable_format_omitted(self, backend):
        # A live String whose FormatName is Json (apply cannot create it) must NOT
        # emit format_name — the column round-trips, re-created as the default Text.
        info = {
            "SchemaName": "new_Payload",
            "DisplayName": _label("Payload"),
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "None"},
            "MaxLength": 4000,
            "FormatName": {"Value": "Json"},
        }
        attrs = {"value": [_shallow("new_name"), _shallow("new_payload")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_payload"), json=info)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["kind"] == "string"
        assert col["schema_name"] == "new_Payload"
        assert "format_name" not in col  # Json dropped
        apply.validate_spec(spec)  # must not raise
        # And the projected attr survives add_attribute's stricter string check.
        from crm.core.metadata_attrs import _string_attr
        _string_attr({**col, "logical_name": col["schema_name"].lower()})

    def test_string_supported_format_retained(self, backend):
        # Regression guard: a supported format (Email) is still emitted.
        info = {
            "SchemaName": "new_Contact",
            "DisplayName": _label("Contact"),
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "None"},
            "MaxLength": 100,
            "FormatName": {"Value": "Email"},
        }
        attrs = {"value": [_shallow("new_name"), _shallow("new_contact")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_contact"), json=info)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["format_name"] == "Email"

    def test_string_missing_max_length_skipped(self, backend):
        # Sparse/permission-limited deep read with no MaxLength -> attribute skipped
        # (apply makes max_length mandatory for string/memo).
        info = {
            "SchemaName": "new_Sparse",
            "DisplayName": _label("Sparse"),
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "None"},
            # no MaxLength
        }
        attrs = {"value": [_shallow("new_name"), _shallow("new_sparse")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_sparse"), json=info)
            spec = build_entity_spec(backend, "new_project")

        assert "attributes" not in spec["entities"][0]
        apply.validate_spec(spec)  # must not raise


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

    def test_decimal_missing_precision_skipped(self, backend):
        # Sparse/permission-limited deep read with no Precision -> attribute skipped
        # (apply makes precision mandatory for decimal/double/money).
        info = {
            "SchemaName": "new_Sparse",
            "DisplayName": _label("Sparse"),
            "AttributeTypeName": {"Value": "DecimalType"},
            "RequiredLevel": {"Value": "None"},
            # no Precision
        }
        attrs = {"value": [_shallow("new_name"), _shallow("new_sparse")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_sparse"), json=info)
            spec = build_entity_spec(backend, "new_project")

        assert "attributes" not in spec["entities"][0]
        apply.validate_spec(spec)  # must not raise


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


class TestUnresolvedPicklistSkipped:
    def test_local_picklist_empty_options_skipped(self, backend):
        # Local OptionSet with NO options (and no GlobalOptionSet) -> skipped.
        attrs = {"value": [_shallow("new_name"), _shallow("new_stage")]}
        cast = {
            "LogicalName": "new_stage",
            "OptionSet": {"Options": []},
            "GlobalOptionSet": None,
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_stage"), json=_local_pick_info())
            m.get(_pick_cast_url(backend, "new_stage"), json=cast)
            spec = build_entity_spec(backend, "new_project")

        # The unresolved picklist is absent; no bare picklist / options:[] emitted.
        assert "attributes" not in spec["entities"][0]
        assert "optionsets" not in spec
        apply.validate_spec(spec)  # must not raise

    def test_both_null_cast_skipped(self, backend):
        # Sparse/permission-limited read: OptionSet null AND GlobalOptionSet null.
        attrs = {"value": [_shallow("new_name"), _shallow("new_stage")]}
        cast = {
            "LogicalName": "new_stage",
            "OptionSet": None,
            "GlobalOptionSet": None,
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_stage"), json=_local_pick_info())
            m.get(_pick_cast_url(backend, "new_stage"), json=cast)
            spec = build_entity_spec(backend, "new_project")

        assert "attributes" not in spec["entities"][0]
        assert "optionsets" not in spec
        apply.validate_spec(spec)  # must not raise

    def test_cast_read_404_skips_attribute_rest_of_entity_exports(self, backend):
        # A picklist whose cast GET returns 404 (permission-limited metadata) must
        # be silently dropped; the remaining attributes still export and
        # validate_spec passes on the result.
        attrs = {"value": [
            _shallow("new_name"),
            _shallow("new_stage"),   # picklist — cast will 404 → dropped
            _shallow("new_code"),    # string — must still export
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_stage"), json=_local_pick_info())
            m.get(_pick_cast_url(backend, "new_stage"), status_code=404,
                  json={"error": {"message": "Not Found"}})
            m.get(_attr_url(backend, "new_code"), json=_string_info())
            spec = build_entity_spec(backend, "new_project")

        # The picklist is absent; the string column is still present.
        cols = spec["entities"][0]["attributes"]
        assert [c["schema_name"] for c in cols] == ["new_Code"]
        assert "optionsets" not in spec
        apply.validate_spec(spec)  # must not raise


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


class TestMultiselect:
    def test_local_multiselect_inline_options(self, backend):
        # A multiselect bound to a LOCAL option set is read via the MultiSelect
        # cast (NOT the Picklist cast) and emits inline options.
        attrs = {"value": [_shallow("new_name"), _shallow("new_tags")]}
        cast = {
            "LogicalName": "new_tags",
            "OptionSet": {"Options": [_opt(1, "Red"), _opt(2, "Blue")]},
            "GlobalOptionSet": None,
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_tags"), json=_multiselect_info())
            m.get(_multi_cast_url(backend, "new_tags"), json=cast)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["kind"] == "multiselect"
        assert col["options"] == [
            {"value": 1, "label": "Red"},
            {"value": 2, "label": "Blue"},
        ]
        assert "optionset_name" not in col
        assert "optionsets" not in spec
        apply.validate_spec(spec)  # must not raise

    def test_global_multiselect_emits_optionset_name(self, backend):
        # A multiselect bound to a GLOBAL set emits optionset_name + a top-level
        # optionsets entry (read via the MultiSelect cast, the global set via the
        # GlobalOptionSetDefinitions endpoint).
        attrs = {"value": [_shallow("new_name"), _shallow("new_tags")]}
        cast = {
            "LogicalName": "new_tags",
            "OptionSet": None,
            "GlobalOptionSet": {"Name": "new_tagset", "IsGlobal": True},
        }
        gos = {
            "Name": "new_tagset",
            "DisplayName": _label("Tag Set"),
            "Options": [_opt(100, "Alpha"), _opt(200, "Beta")],
        }
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_tags"), json=_multiselect_info())
            m.get(_multi_cast_url(backend, "new_tags"), json=cast)
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_tagset')"),
                json=gos,
            )
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["kind"] == "multiselect"
        assert col["optionset_name"] == "new_tagset"
        assert "options" not in col
        assert len(spec["optionsets"]) == 1
        os_entry = spec["optionsets"][0]
        assert os_entry["name"] == "new_tagset"
        assert os_entry["display_name"] == "Tag Set"
        assert os_entry["options"] == [
            {"value": 100, "label": "Alpha"},
            {"value": 200, "label": "Beta"},
        ]
        apply.validate_spec(spec)  # must not raise


class TestEmptyLabelFallback:
    def test_attribute_display_name_falls_back_to_schema(self, backend):
        # An attribute whose DisplayName is absent -> display_name == schema_name.
        info = {
            "SchemaName": "new_Code",
            # no DisplayName
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "None"},
            "MaxLength": 50,
            "FormatName": {"Value": "Text"},
        }
        attrs = {"value": [_shallow("new_name"), _shallow("new_code")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=info)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["display_name"] == "new_Code"
        apply.validate_spec(spec)  # must not raise

    def test_entity_display_name_falls_back_to_schema(self, backend):
        # An entity with no DisplayName -> entity display_name == schema_name.
        ent = {
            "LogicalName": "new_project",
            "SchemaName": "new_Project",
            # no DisplayName
            "OwnershipType": "UserOwned",
            "PrimaryNameAttribute": "new_name",
        }
        attrs = {"value": [_shallow("new_name")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=ent)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            spec = build_entity_spec(backend, "new_project")

        assert spec["entities"][0]["display_name"] == "new_Project"
        apply.validate_spec(spec)  # must not raise

    def test_primary_attr_label_falls_back_to_schema(self, backend):
        # Primary attribute with no DisplayName -> label falls back to schema name.
        primary = {
            "SchemaName": "new_Name",
            # no DisplayName
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "ApplicationRequired"},
            "MaxLength": 200,
            "FormatName": {"Value": "Text"},
        }
        attrs = {"value": [_shallow("new_name")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=primary)
            spec = build_entity_spec(backend, "new_project")

        assert spec["entities"][0]["primary_attr"] == {
            "schema_name": "new_Name",
            "label": "new_Name",
        }
        apply.validate_spec(spec)  # must not raise


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

    def test_with_views_filters_empty_columns_and_empty_name(self, backend):
        # Views with empty columns (unparseable layoutxml) OR empty name are both
        # dropped; only the fully-valid view survives and validate_spec passes.
        from crm.core.views import _build_layoutxml, _build_fetchxml
        attrs = {"value": [_shallow("new_name")]}
        cols = [("new_name", 200)]
        good_layout = _build_layoutxml("new_project", 10042, cols)
        good_fetch = _build_fetchxml("new_project", cols, "new_name", False)
        savedqueries = {"value": [
            {
                "name": "Empty View",        # empty columns → dropped
                "layoutxml": "",
                "fetchxml": "",
                "isdefault": False,
            },
            {
                "name": "",                  # empty name → dropped
                "layoutxml": good_layout,
                "fetchxml": good_fetch,
                "isdefault": False,
            },
            {
                "name": "Active Projects",   # valid → kept
                "layoutxml": good_layout,
                "fetchxml": good_fetch,
                "isdefault": True,
            },
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(backend.url_for("savedqueries"), json=savedqueries)
            spec = build_entity_spec(backend, "new_project", with_views=True)

        ent_views = spec["entities"][0]["views"]
        assert [v["name"] for v in ent_views] == ["Active Projects"]
        apply.validate_spec(spec)  # must not raise

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


class TestExportSpecWarnings:
    def test_unmapped_type_warns(self, backend):
        # An attribute whose AttributeTypeName.Value maps to no kind (metadata_constraints.kind_for_type_name → None).
        attrs = {"value": [_shallow("new_name"), _shallow("new_weird")]}
        weird = {
            "SchemaName": "new_Weird",
            "DisplayName": _label("Weird"),
            "AttributeTypeName": {"Value": "ManagedPropertyType"},
            "RequiredLevel": {"Value": "None"},
        }
        warnings: list[str] = []
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_weird"), json=weird)
            spec = build_entity_spec(backend, "new_project", warnings=warnings)

        assert "attributes" not in spec["entities"][0]
        assert len(warnings) == 1
        assert "new_weird" in warnings[0]
        assert "ManagedPropertyType" in warnings[0]

    def test_string_missing_maxlength_warns(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_code")]}
        no_len = {
            "SchemaName": "new_Code",
            "DisplayName": _label("Code"),
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "None"},
            # MaxLength deliberately absent (sparse read)
        }
        warnings: list[str] = []
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=no_len)
            spec = build_entity_spec(backend, "new_project", warnings=warnings)

        assert "attributes" not in spec["entities"][0]
        assert len(warnings) == 1
        assert "new_code" in warnings[0]
        assert "MaxLength" in warnings[0]

    def test_precision_missing_warns(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_budget")]}
        no_prec = {
            "SchemaName": "new_Budget",
            "DisplayName": _label("Budget"),
            "AttributeTypeName": {"Value": "DecimalType"},
            "RequiredLevel": {"Value": "None"},
            # Precision absent
        }
        warnings: list[str] = []
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_budget"), json=no_prec)
            spec = build_entity_spec(backend, "new_project", warnings=warnings)

        assert "attributes" not in spec["entities"][0]
        assert len(warnings) == 1
        assert "new_budget" in warnings[0]
        assert "Precision" in warnings[0]

    def test_lookup_no_target_warns(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_accountid")]}
        no_target = {
            "SchemaName": "new_AccountId",
            "DisplayName": _label("Account"),
            "AttributeTypeName": {"Value": "LookupType"},
            "RequiredLevel": {"Value": "None"},
            "Targets": [],
        }
        warnings: list[str] = []
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_accountid"), json=no_target)
            spec = build_entity_spec(backend, "new_project", warnings=warnings)

        assert "attributes" not in spec["entities"][0]
        assert len(warnings) == 1
        assert "new_accountid" in warnings[0]
        assert "target" in warnings[0].lower()

    def test_picklist_cast_failure_warns(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_stage")]}
        warnings: list[str] = []
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_stage"), json=_local_pick_info())
            # The Picklist cast read fails (e.g. 403 / not castable on this build).
            m.get(_pick_cast_url(backend, "new_stage"), status_code=403, json={
                "error": {"code": "0x80040220", "message": "forbidden"}
            })
            spec = build_entity_spec(backend, "new_project", warnings=warnings)

        assert "attributes" not in spec["entities"][0]
        assert len(warnings) == 1
        assert "new_stage" in warnings[0]

    def test_empty_options_warns(self, backend):
        attrs = {"value": [_shallow("new_name"), _shallow("new_stage")]}
        warnings: list[str] = []
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_stage"), json=_local_pick_info())
            # Cast succeeds but the local option set has no options.
            m.get(_pick_cast_url(backend, "new_stage"), json={
                "OptionSet": {"Options": []},
            })
            spec = build_entity_spec(backend, "new_project", warnings=warnings)

        assert "attributes" not in spec["entities"][0]
        assert len(warnings) == 1
        assert "new_stage" in warnings[0]
