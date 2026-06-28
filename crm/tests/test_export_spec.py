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
from crm.core.export_spec import build_entity_spec, build_solution_spec


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


def _shallow(logical, *, custom=True, valid_for_create=True, source_type=None):
    # list_attributes always projects IsValidForCreate + SourceType; mirror that
    # here so the build_entity_spec creatability filter sees a faithful shallow row.
    row = {
        "LogicalName": logical,
        "SchemaName": logical,
        "IsCustomAttribute": custom,
        "IsValidForCreate": valid_for_create,
    }
    if source_type is not None:
        row["SourceType"] = source_type
    return row


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


class TestUnresolvedPicklistSkipped:
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

    def test_uncreatable_companion_attrs_excluded(self, backend):
        # A lookup's server-auto-generated …Name/…YomiName companions carry
        # IsCustomAttribute=true but IsValidForCreate=false (#497): they are not
        # independently creatable, so the spec must skip them — otherwise the
        # clone re-creates them standalone and re-creating the parent lookup
        # collides ("attribute …Name already exists"). They are never deep-read
        # (no _attr_url mock registered → requests_mock raises if one is fetched).
        attrs = {"value": [
            _shallow("new_name"),                              # primary
            _shallow("new_accountid"),                         # creatable lookup
            _shallow("new_accountidname", valid_for_create=False),      # companion
            _shallow("new_accountidyominame", valid_for_create=False),  # companion
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_accountid"), json=_lookup_info())
            spec = build_entity_spec(backend, "new_project")

        cols = spec["entities"][0]["attributes"]
        assert [c["schema_name"] for c in cols] == ["new_AccountId"]


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
        from crm.core.views import build_layoutxml, build_fetchxml
        attrs = {"value": [_shallow("new_name")]}
        cols = [("new_name", 200)]
        layoutxml = build_layoutxml("new_project", 10042, cols)
        fetchxml = build_fetchxml("new_project", cols, "new_name", False)
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

    def test_view_filter_active_round_trips_through_validate_spec(self, backend):
        """Acceptance: a view whose fetchxml filters to active records exports
        `filter_active: True`, and the spec passes validate_spec — so the
        active-only filter survives export-spec → apply."""
        from crm.core.views import build_layoutxml, build_fetchxml
        attrs = {"value": [_shallow("new_name")]}
        cols = [("new_name", 200)]
        savedqueries = {"value": [{
            "name": "Active Projects",
            "layoutxml": build_layoutxml("new_project", 10042, cols),
            "fetchxml": build_fetchxml("new_project", cols, "new_name", True, True),
            "isdefault": False,
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(backend.url_for("savedqueries"), json=savedqueries)
            spec = build_entity_spec(backend, "new_project", with_views=True)

        view = spec["entities"][0]["views"][0]
        assert view["filter_active"] is True
        assert view["order_desc"] is True
        apply.validate_spec(spec)  # must not raise

    def test_with_views_filters_empty_columns_and_empty_name(self, backend):
        # Views with empty columns (unparseable layoutxml) OR empty name are both
        # dropped; only the fully-valid view survives and validate_spec passes.
        from crm.core.views import build_layoutxml, build_fetchxml
        attrs = {"value": [_shallow("new_name")]}
        cols = [("new_name", 200)]
        good_layout = build_layoutxml("new_project", 10042, cols)
        good_fetch = build_fetchxml("new_project", cols, "new_name", False)
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

    def test_non_default_cascade_round_trips_through_validate_spec(self, backend):
        """Acceptance: a relationship with a non-default cascade exports the flat
        cascade_* keys the apply adapter consumes, and the spec passes
        validate_spec — so export-spec → apply does not silently reset cascade."""
        attrs = {"value": [_shallow("new_name")]}
        o2m = {"value": [{
            "SchemaName": "new_project_new_task",
            "ReferencedEntity": "new_project",
            "ReferencingEntity": "new_task",
            "ReferencingAttribute": "new_projectid",
            "IsCustomRelationship": True,
            "CascadeConfiguration": {"Assign": "Cascade", "Delete": "Cascade"},
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
            m.get(_o2m_url(backend), json=o2m)
            m.get(_attr_url(backend, "new_projectid", entity="new_task"), json=rel_attr)
            spec = build_entity_spec(backend, "new_project", with_relationships=True)

        rel = spec["entities"][0]["relationships"][0]
        assert rel["cascade_assign"] == "Cascade"
        assert rel["cascade_delete"] == "Cascade"
        apply.validate_spec(spec)  # must not raise


class TestWiderAttributeFields:
    """Attribute kwargs the apply adapter accepts, emitted from the deep-read when
    non-default (auto_number_format / behavior_name / max_size_kb / int bounds)."""

    def _build_one(self, backend, info):
        attrs = {"value": [_shallow("new_name"), _shallow("new_col")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_col"), json=info)
            spec = build_entity_spec(backend, "new_project")
        apply.validate_spec(spec)
        return spec["entities"][0]["attributes"][0]

    def test_auto_number_format_emitted(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "None"},
            "MaxLength": 100, "FormatName": {"Value": "Text"},
            "AutoNumberFormat": "INV-{SEQNUM:5}",
        }
        assert self._build_one(backend, info)["auto_number_format"] == "INV-{SEQNUM:5}"

    def test_string_without_auto_number_omits_key(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "StringType"},
            "RequiredLevel": {"Value": "None"},
            "MaxLength": 100, "FormatName": {"Value": "Text"},
        }
        assert "auto_number_format" not in self._build_one(backend, info)

    def test_integer_non_default_bounds_emitted(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "IntegerType"},
            "RequiredLevel": {"Value": "None"},
            "MinValue": 0, "MaxValue": 100,
        }
        attr = self._build_one(backend, info)
        assert attr["min_value"] == 0
        assert attr["max_value"] == 100

    def test_integer_full_range_bounds_omitted(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "IntegerType"},
            "RequiredLevel": {"Value": "None"},
            "MinValue": -2147483648, "MaxValue": 2147483647,
        }
        attr = self._build_one(backend, info)
        assert "min_value" not in attr and "max_value" not in attr

    def test_datetime_behavior_emitted(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "DateTimeType"},
            "RequiredLevel": {"Value": "None"},
            "DateTimeBehavior": {"Value": "DateOnly"},
        }
        assert self._build_one(backend, info)["behavior_name"] == "DateOnly"

    def test_datetime_default_behavior_omitted(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "DateTimeType"},
            "RequiredLevel": {"Value": "None"},
            "DateTimeBehavior": {"Value": "UserLocal"},
        }
        assert "behavior_name" not in self._build_one(backend, info)

    def test_file_max_size_emitted_when_non_default(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "FileType"},
            "RequiredLevel": {"Value": "None"},
            "MaxSizeInKB": 10240,
        }
        assert self._build_one(backend, info)["max_size_kb"] == 10240

    def test_file_default_max_size_omitted(self, backend):
        info = {
            "SchemaName": "new_Col", "DisplayName": _label("Col"),
            "AttributeTypeName": {"Value": "FileType"},
            "RequiredLevel": {"Value": "None"},
            "MaxSizeInKB": 32768,
        }
        assert "max_size_kb" not in self._build_one(backend, info)


class TestWiderEntityFields:
    """Entity-level kwargs the apply adapter accepts, emitted when non-default."""

    def _build(self, backend, *, entity=None, primary=None):
        attrs = {"value": [_shallow("new_name")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=entity or _ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=primary or _primary_info())
            spec = build_entity_spec(backend, "new_project")
        apply.validate_spec(spec)
        return spec["entities"][0]

    def test_has_notes_and_activities_emitted_when_true(self, backend):
        ent = dict(_ENTITY, HasNotes=True, HasActivities=True)
        out = self._build(backend, entity=ent)
        assert out["has_notes"] is True
        assert out["has_activities"] is True

    def test_has_notes_and_activities_omitted_when_false(self, backend):
        ent = dict(_ENTITY, HasNotes=False, HasActivities=False)
        out = self._build(backend, entity=ent)
        assert "has_notes" not in out
        assert "has_activities" not in out

    def test_primary_attr_max_length_emitted_when_non_default(self, backend):
        primary = dict(_primary_info(), MaxLength=150)
        out = self._build(backend, primary=primary)
        assert out["primary_attr_max_length"] == 150

    def test_primary_attr_max_length_omitted_at_default(self, backend):
        # _primary_info() returns MaxLength 200, the create_entity default.
        out = self._build(backend)
        assert "primary_attr_max_length" not in out


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
        from crm.core.views import build_layoutxml, build_fetchxml
        cols = [("new_name", 200)]
        savedqueries = {"value": [{
            "name": "All",
            "layoutxml": build_layoutxml("new_project", 10042, cols),
            "fetchxml": build_fetchxml("new_project", cols, None, False),
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
        apply.validate_spec(spec)  # skipped attr → spec still validates

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
        apply.validate_spec(spec)  # skipped attr → spec still validates

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

    def test_calculated_column_exported_with_formula(self, backend):
        # A calculated column (SourceType=1) round-trips: source_type + the live
        # FormulaDefinition are captured so apply can re-create it (#554).
        info = {
            "SchemaName": "new_Total",
            "DisplayName": _label("Total"),
            "AttributeTypeName": {"Value": "DecimalType"},
            "RequiredLevel": {"Value": "None"},
            "Precision": 2,
            "SourceType": 1,
            "FormulaDefinition": "<Formula>calc</Formula>",
        }
        # Calc/rollup columns are read-only → IsValidForCreate is False; SourceType
        # rides on the shallow row so the filter can still admit them.
        attrs = {"value": [_shallow("new_name"),
                           _shallow("new_total", valid_for_create=False, source_type=1)]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_total"), json=info)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["kind"] == "decimal"
        assert col["source_type"] == "calculated"
        assert col["formula_definition"] == "<Formula>calc</Formula>"
        apply.validate_spec(spec)  # the round-trip must hold

    def test_rollup_column_exported_with_formula(self, backend):
        # A rollup column reads back as SourceType=2.
        info = {
            "SchemaName": "new_Count",
            "DisplayName": _label("Count"),
            "AttributeTypeName": {"Value": "IntegerType"},
            "RequiredLevel": {"Value": "None"},
            "SourceType": 2,
            "FormulaDefinition": "<Rollup/>",
        }
        attrs = {"value": [_shallow("new_name"),
                           _shallow("new_count", valid_for_create=False, source_type=2)]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_count"), json=info)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert col["source_type"] == "rollup"
        assert col["formula_definition"] == "<Rollup/>"
        apply.validate_spec(spec)

    def test_simple_column_omits_source_type_even_when_keys_present(self, backend):
        # A live simple column carries SourceType=0 and a null FormulaDefinition on
        # the bare read; neither must leak into the spec.
        info = {**_string_info(), "SourceType": 0, "FormulaDefinition": None}
        attrs = {"value": [_shallow("new_name"), _shallow("new_code")]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=info)
            spec = build_entity_spec(backend, "new_project")

        col = spec["entities"][0]["attributes"][0]
        assert "source_type" not in col
        assert "formula_definition" not in col

    def test_calculated_without_readable_formula_warns_and_exports_simple(self, backend):
        # SourceType says calculated but the formula is unreadable (empty): the
        # column still round-trips as simple, and the drop is recorded.
        info = {**_string_info(), "SourceType": 1, "FormulaDefinition": ""}
        attrs = {"value": [_shallow("new_name"),
                           _shallow("new_code", valid_for_create=False, source_type=1)]}
        warnings: list[str] = []
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_code"), json=info)
            spec = build_entity_spec(backend, "new_project", warnings=warnings)

        col = spec["entities"][0]["attributes"][0]
        assert "source_type" not in col
        assert any("FormulaDefinition" in w for w in warnings)
        apply.validate_spec(spec)

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


class TestCalcRoundTrip:
    def test_export_then_apply_dry_run_reports_zero_drift(self, backend, dry_backend):
        # AC#3 literal round-trip: build_entity_spec of an entity carrying a calc
        # column, then apply --dry-run of that EXACT export against the same live
        # state, reports zero drift — the calc column reconciles to skipped (#554).
        calc_info = {
            "SchemaName": "new_Total",
            "DisplayName": _label("Total"),
            "AttributeTypeName": {"Value": "DecimalType"},
            "@odata.type": "#Microsoft.Dynamics.CRM.DecimalAttributeMetadata",
            "RequiredLevel": {"Value": "None"},
            "Precision": 2,
            "SourceType": 1,
            "FormulaDefinition": "<Formula>calc</Formula>",
        }
        attrs = {"value": [_shallow("new_name"),
                           _shallow("new_total", valid_for_create=False, source_type=1)]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_attr_url(backend, "new_name"), json=_primary_info())
            m.get(_attr_url(backend, "new_total"), json=calc_info)
            spec = build_entity_spec(backend, "new_project")
            res = apply.apply_spec(dry_backend, spec, stage_only=True)

        calc = next(a for a in spec["entities"][0]["attributes"]
                    if a["schema_name"] == "new_Total")
        assert calc["source_type"] == "calculated"
        assert calc["formula_definition"] == "<Formula>calc</Formula>"
        # Zero drift: nothing updated or replace-blocked; the calc column is skipped.
        assert [e["name"] for e in res["updated"]] == []
        assert [e["name"] for e in res["replace_blocked"]] == []
        assert "new_Total" in [e["name"] for e in res["skipped"]]
        assert res["ok"] is True


# ── solution-level projection (build_solution_spec, #613) ─────────────────────

# Obvious placeholder GUIDs — never a real org's identifiers (public repo).
_SOL_ID = "22222222-2222-2222-2222-222222222222"
_ENTITY_MD_ID = "11111111-1111-1111-1111-111111111111"


def _solutions_url(backend) -> str:
    return backend.url_for("solutions")


def _components_url(backend) -> str:
    return backend.url_for("solutioncomponents")


def _entity_by_id_url(backend, md_id=_ENTITY_MD_ID) -> str:
    return backend.url_for(f"EntityDefinitions({md_id})")


def _solution(unique="myorgsln"):
    return {"value": [{"solutionid": _SOL_ID, "uniquename": unique}]}


def _members(*rows):
    return {"value": list(rows)}


def _member(componenttype, objectid, behavior=0):
    return {"componenttype": componenttype, "objectid": objectid,
            "rootcomponentbehavior": behavior}


def _mock_minimal_entity(m, backend, *, logical="new_project"):
    """Mock the GETs build_entity_spec makes for a primary-attr-only entity
    with views+relationships enabled (both empty)."""
    m.get(_entity_url(backend, logical), json=_ENTITY)
    m.get(_attrs_url(backend, logical), json={"value": [_shallow("new_name")]})
    m.get(_attr_url(backend, "new_name", logical), json=_primary_info())
    m.get(_o2m_url(backend, logical), json={"value": []})
    m.get(backend.url_for("savedqueries"), json={"value": []})


class TestSolutionLevel:
    def test_one_entity_member_projects_and_round_trips(self, backend, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(_solutions_url(backend), json=_solution())
            m.get(_components_url(backend),
                  json=_members(_member(1, _ENTITY_MD_ID)))
            m.get(_entity_by_id_url(backend), json={"LogicalName": "new_project"})
            _mock_minimal_entity(m, backend)
            result = build_solution_spec(backend, "myorgsln")

        spec = result["spec"]
        # Top-level solution key is a dict (validate_spec / apply auto-scope).
        assert spec["solution"] == {"unique_name": "myorgsln"}
        assert [e["schema_name"] for e in spec["entities"]] == ["new_Project"]
        assert result["skipped"] == []
        # Load-bearing round-trip: the merged spec validates.
        apply.validate_spec(spec)

    def test_non_entity_members_go_to_skipped_and_verb_succeeds(self, backend):
        # Plug-in assembly, security role, and an a-la-carte attribute member.
        with requests_mock.Mocker() as m:
            m.get(_solutions_url(backend), json=_solution())
            m.get(_components_url(backend), json=_members(
                _member(90, "33333333-3333-3333-3333-333333333330"),  # plugintype
                _member(91, "33333333-3333-3333-3333-333333333331"),  # pluginassembly
                _member(92, "33333333-3333-3333-3333-333333333332"),  # sdkmessageprocessingstep
                _member(20, "44444444-4444-4444-4444-444444444444"),  # role
                _member(2,  "55555555-5555-5555-5555-555555555555"),  # attribute
            ))
            result = build_solution_spec(backend, "myorgsln")

        # No entity members -> empty entities, verb still succeeds (no raise).
        assert result["spec"]["entities"] == []
        by_type = {s["type"]: s for s in result["skipped"]}
        assert set(by_type) == {
            "plugintype", "pluginassembly", "sdkmessageprocessingstep", "role", "attribute"}
        # All three plug-in component types share the assembly-bytes reason — it is
        # accurate for the assembly AND its dependent type/step rows.
        for t in ("plugintype", "pluginassembly", "sdkmessageprocessingstep"):
            assert "not projectable from a live org" in by_type[t]["reason"]
        assert "deferred to a follow-up slice" in by_type["role"]["reason"]
        assert "known simplification" in by_type["attribute"]["reason"]
        assert by_type["role"]["objectid"] == "44444444-4444-4444-4444-444444444444"

    def test_entity_resolution_failure_is_skipped_not_fatal(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_solutions_url(backend), json=_solution())
            m.get(_components_url(backend), json=_members(_member(1, _ENTITY_MD_ID)))
            m.get(_entity_by_id_url(backend), status_code=404)
            result = build_solution_spec(backend, "myorgsln")

        assert result["spec"]["entities"] == []
        assert len(result["skipped"]) == 1
        s = result["skipped"][0]
        assert s["type"] == "entity"
        assert s["objectid"] == _ENTITY_MD_ID
        assert "could not resolve entity metadata id" in s["reason"]

    def test_global_optionset_deduped_across_two_entities(self, backend):
        # Two entity members, each with a global picklist bound to the SAME set.
        md_a = _ENTITY_MD_ID
        md_b = "66666666-6666-6666-6666-666666666666"
        glob = {"Name": "new_priorityset", "IsGlobal": True}
        gos = {
            "Name": "new_priorityset",
            "DisplayName": _label("Priority"),
            "Options": [_opt(10, "Low"), _opt(20, "High")],
        }
        with requests_mock.Mocker() as m:
            m.get(_solutions_url(backend), json=_solution())
            m.get(_components_url(backend),
                  json=_members(_member(1, md_a), _member(1, md_b)))
            m.get(_entity_by_id_url(backend, md_a), json={"LogicalName": "new_project"})
            m.get(_entity_by_id_url(backend, md_b), json={"LogicalName": "new_task"})
            m.get(backend.url_for("savedqueries"), json={"value": []})
            # entity new_project
            m.get(_entity_url(backend, "new_project"), json=_ENTITY)
            m.get(_attrs_url(backend, "new_project"),
                  json={"value": [_shallow("new_name"), _shallow("new_priority")]})
            m.get(_attr_url(backend, "new_name", "new_project"), json=_primary_info())
            m.get(_attr_url(backend, "new_priority", "new_project"),
                  json=_global_pick_info("new_Priority"))
            m.get(_pick_cast_url(backend, "new_priority", "new_project"),
                  json={"OptionSet": None, "GlobalOptionSet": glob})
            m.get(_o2m_url(backend, "new_project"), json={"value": []})
            # entity new_task (its own SchemaName so the merged spec has two entities)
            task_ent = {**_ENTITY, "LogicalName": "new_task", "SchemaName": "new_Task"}
            m.get(_entity_url(backend, "new_task"), json=task_ent)
            m.get(_attrs_url(backend, "new_task"),
                  json={"value": [_shallow("new_name"), _shallow("new_rank")]})
            m.get(_attr_url(backend, "new_name", "new_task"), json=_primary_info())
            m.get(_attr_url(backend, "new_rank", "new_task"),
                  json=_global_pick_info("new_Rank"))
            m.get(_pick_cast_url(backend, "new_rank", "new_task"),
                  json={"OptionSet": None, "GlobalOptionSet": glob})
            m.get(_o2m_url(backend, "new_task"), json={"value": []})
            m.get(backend.url_for("GlobalOptionSetDefinitions(Name='new_priorityset')"),
                  json=gos)
            result = build_solution_spec(backend, "myorgsln")

        spec = result["spec"]
        assert sorted(e["schema_name"] for e in spec["entities"]) == ["new_Project", "new_Task"]
        # Referenced by both entities, emitted exactly once.
        assert len(spec["optionsets"]) == 1
        assert spec["optionsets"][0]["name"] == "new_priorityset"
        apply.validate_spec(spec)

    def test_round_trips_through_dry_run_apply(self, backend, dry_backend):
        # Build the merged spec, then prove it round-trips through a --dry-run
        # apply_spec against a mocked target org without error (AC: apply-seedable).
        with requests_mock.Mocker() as m:
            m.get(_solutions_url(backend), json=_solution())
            m.get(_components_url(backend), json=_members(_member(1, _ENTITY_MD_ID)))
            m.get(_entity_by_id_url(backend), json={"LogicalName": "new_project"})
            _mock_minimal_entity(m, backend)
            spec = build_solution_spec(backend, "myorgsln")["spec"]

        with requests_mock.Mocker() as m:
            # Entity absent in the target -> dry-run plans its creation.
            m.get(_entity_url(backend), status_code=404)
            # Prune detection (runs under dry_run) reads the target solution.
            m.get(_solutions_url(backend), json=_solution())
            m.get(_components_url(backend), json=_members())
            res = apply.apply_spec(dry_backend, spec)

        assert res["failed"] == []
        assert "new_Project" in [e["name"] for e in res["planned"]]

    def test_malformed_componenttype_is_skipped_not_dropped(self, backend):
        # A row whose componenttype is not an int must surface in skipped, not be
        # silently dropped (the never-drop-silently invariant, ADR 0019).
        with requests_mock.Mocker() as m:
            m.get(_solutions_url(backend), json=_solution())
            m.get(_components_url(backend),
                  json=_members({"componenttype": None, "objectid": "bad-row"}))
            result = build_solution_spec(backend, "myorgsln")

        assert result["spec"]["entities"] == []
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["objectid"] == "bad-row"
        assert "non-integer componenttype" in result["skipped"][0]["reason"]
