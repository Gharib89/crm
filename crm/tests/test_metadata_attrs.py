"""Unit tests for crm.core.metadata_attrs."""
# pyright: basic

from __future__ import annotations

from typing import Any

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


_ATTR_ID = "33333333-3333-3333-3333-333333333333"


def _post_body(m):
    """Return the JSON body of the first POST request (skips the existence probe GET)."""
    for r in m.request_history:
        if r.method == "POST":
            return r.json()
    raise AssertionError("no POST request recorded")


def _mock_post_and_readback(m, backend, entity: str, attr_logical: str,
                            attr_type: str = "String"):
    # The --if-exists existence probe issues a GET against the attribute path
    # before POSTing; mock it as 404 (not present) so the create proceeds.
    m.get(
        backend.url_for(
            f"EntityDefinitions(LogicalName='{entity}')"
            f"/Attributes(LogicalName='{attr_logical}')"
        ),
        status_code=404,
        json={"error": {"code": "0x", "message": "not found"}},
    )
    attr_url = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes({_ATTR_ID})"
    )
    m.post(
        backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes"),
        status_code=204,
        headers={"OData-EntityId": attr_url},
    )
    m.get(
        attr_url,
        json={
            "LogicalName": attr_logical,
            "SchemaName": attr_logical,
            "AttributeType": attr_type,
        },
    )
    return attr_url


class TestAddAttributeString:
    def test_string_posts_correct_payload(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_label", "String")
            info = ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Label",
                max_length=100,
            )
        assert info["created"] is True
        assert info["attribute_type"] == "String"
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.StringAttributeMetadata"
        assert body["MaxLength"] == 100
        assert body["FormatName"]["Value"] == "Text"

    def test_string_requires_max_length(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="max-length"):
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Label",
            )

    def test_string_rejects_precision_flag(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="precision"):
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Label",
                max_length=100,
                precision=2,
            )


class TestAddAttributeMemo:
    def test_memo_posts_correct_payload(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_notes", "Memo")
            info = ma.add_attribute(
                backend,
                entity="new_widget",
                kind="memo",
                schema_name="new_Notes",
                display_name="Notes",
                max_length=4000,
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.MemoAttributeMetadata"
        assert body["MaxLength"] == 4000

    def test_memo_requires_max_length(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="max-length"):
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="memo",
                schema_name="new_Notes",
                display_name="Notes",
            )


class TestAddAttributeNonAsciiLabel:
    def test_unicode_label_passes_through(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_label", "String")
            ma.add_attribute(
                backend,
                entity="new_widget",
                kind="string",
                schema_name="new_Label",
                display_name="Étiquette — niño",
                max_length=100,
            )
        body = _post_body(m)
        assert body["DisplayName"]["LocalizedLabels"][0]["Label"] == "Étiquette — niño"


class TestAddAttributeNumeric:
    def test_integer_with_min_max(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_qty", "Integer")
            ma.add_attribute(
                backend, entity="new_widget", kind="integer",
                schema_name="new_Qty", display_name="Qty",
                min_value=0, max_value=1000,
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.IntegerAttributeMetadata"
        assert body["MinValue"] == 0
        assert body["MaxValue"] == 1000

    def test_integer_min_max_coerced_to_int(self, backend):
        # The CLI parses --min/--max as floats, so a bound of 0 arrives as 0.0 and
        # would serialize to Edm.Decimal "0.0", which the server rejects for an
        # Edm.Int32 column. Bounds must serialize as plain integers.
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_qty", "Integer")
            ma.add_attribute(
                backend, entity="new_widget", kind="integer",
                schema_name="new_Qty", display_name="Qty",
                min_value=0.0, max_value=720.0,
            )
        body = _post_body(m)
        assert isinstance(body["MinValue"], int)
        assert isinstance(body["MaxValue"], int)
        assert body["MinValue"] == 0
        assert body["MaxValue"] == 720

    def test_bigint(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_bignum", "BigInt")
            ma.add_attribute(
                backend, entity="new_widget", kind="bigint",
                schema_name="new_Bignum", display_name="Bignum",
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.BigIntAttributeMetadata"

    def test_decimal_requires_precision(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="precision"):
            ma.add_attribute(
                backend, entity="new_widget", kind="decimal",
                schema_name="new_Amount", display_name="Amount",
            )

    def test_decimal_precision_in_range(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_amount", "Decimal")
            ma.add_attribute(
                backend, entity="new_widget", kind="decimal",
                schema_name="new_Amount", display_name="Amount",
                precision=4, min_value=-1000, max_value=1000,
            )
        body = _post_body(m)
        assert body["Precision"] == 4

    def test_decimal_precision_out_of_range(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="precision"):
            ma.add_attribute(
                backend, entity="new_widget", kind="decimal",
                schema_name="new_Amount", display_name="Amount",
                precision=11,
            )

    def test_double(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_rate", "Double")
            ma.add_attribute(
                backend, entity="new_widget", kind="double",
                schema_name="new_Rate", display_name="Rate",
                precision=3,
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.DoubleAttributeMetadata"
        assert body["Precision"] == 3

    def test_money(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_price", "Money")
            ma.add_attribute(
                backend, entity="new_widget", kind="money",
                schema_name="new_Price", display_name="Price",
                precision=2,
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.MoneyAttributeMetadata"
        assert body["Precision"] == 2


class TestAddAttributeBoolean:
    def test_boolean_default_labels(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_active", "Boolean")
            ma.add_attribute(
                backend, entity="new_widget", kind="boolean",
                schema_name="new_Active", display_name="Active",
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.BooleanAttributeMetadata"
        os = body["OptionSet"]
        assert os["TrueOption"]["Value"] == 1
        assert os["TrueOption"]["Label"]["LocalizedLabels"][0]["Label"] == "Yes"
        assert os["FalseOption"]["Value"] == 0
        assert os["FalseOption"]["Label"]["LocalizedLabels"][0]["Label"] == "No"

    def test_boolean_custom_labels_and_default(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_active", "Boolean")
            ma.add_attribute(
                backend, entity="new_widget", kind="boolean",
                schema_name="new_Active", display_name="Active",
                true_label="On", false_label="Off",
                default_value=True,
            )
        body = _post_body(m)
        assert body["DefaultValue"] is True
        assert body["OptionSet"]["TrueOption"]["Label"]["LocalizedLabels"][0]["Label"] == "On"


class TestAddAttributeDateTime:
    def test_datetime_default_format(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_when", "DateTime")
            ma.add_attribute(
                backend, entity="new_widget", kind="datetime",
                schema_name="new_When", display_name="When",
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata"
        assert body["Format"] == "DateAndTime"

    def test_datetime_date_only(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_day", "DateTime")
            ma.add_attribute(
                backend, entity="new_widget", kind="datetime",
                schema_name="new_Day", display_name="Day",
                format_name="DateOnly",
            )
        body = _post_body(m)
        assert body["Format"] == "DateOnly"

    def test_datetime_bad_format_rejected(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="format_name"):
            ma.add_attribute(
                backend, entity="new_widget", kind="datetime",
                schema_name="new_When", display_name="When",
                format_name="Garbage",
            )


class TestAddAttributePicklist:
    def test_picklist_inline_options(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_priority", "Picklist")
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
                options=[(1, "Low"), (2, "Medium"), (3, "High")],
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        opts = body["OptionSet"]["Options"]
        assert opts[0]["Value"] == 1
        assert opts[0]["Label"]["LocalizedLabels"][0]["Label"] == "Low"
        assert body["OptionSet"]["IsGlobal"] is False

    def test_picklist_global_optionset_ref(self, backend):
        from crm.core import metadata_attrs as ma
        os_id = "44444444-4444-4444-4444-444444444444"
        with requests_mock.Mocker() as m:
            # Name -> MetadataId resolution (the bind needs the GUID, not the Name).
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_global_priority')"),
                json={"MetadataId": os_id},
            )
            _mock_post_and_readback(m, backend, "new_widget", "new_priority", "Picklist")
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
                optionset_name="new_global_priority",
            )
        body = _post_body(m)
        # A global option set must bind via the GlobalOptionSet navigation property
        # using the resolved MetadataId GUID; an inline OptionSet with IsGlobal=true
        # is rejected on attribute create.
        assert body["GlobalOptionSet@odata.bind"] == (
            f"GlobalOptionSetDefinitions({os_id})"
        )
        assert "OptionSet" not in body

    def test_picklist_rejects_both_options_and_global(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="mutually exclusive"):
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
                options=[(1, "Low")], optionset_name="new_other",
            )

    def test_picklist_requires_one_of(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="optionset_name or options"):
            ma.add_attribute(
                backend, entity="new_widget", kind="picklist",
                schema_name="new_Priority", display_name="Priority",
            )


class TestAddAttributeMultiselect:
    def test_multiselect_inline(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_tags", "Virtual")
            ma.add_attribute(
                backend, entity="new_widget", kind="multiselect",
                schema_name="new_Tags", display_name="Tags",
                options=[(1, "A"), (2, "B")],
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata"


class TestAddAttributeLookup:
    def test_lookup_dispatches_to_one_to_many(self, backend, monkeypatch):
        from crm.core import metadata_attrs as ma
        from crm.core import relationships as rel
        calls: dict[str, Any] = {}

        def fake_one_to_many(b, **kw):
            calls.update(kw)
            return {
                "created": True, "kind": "OneToMany",
                "schema_name": kw["schema_name"],
                "referencing_attribute": "new_accountid",
                "relationship_id": "rel-id",
                "metadata_id_url": "url",
                "solution": kw.get("solution"),
            }

        monkeypatch.setattr(rel, "create_one_to_many", fake_one_to_many)
        info = ma.add_attribute(
            backend, entity="new_widget", kind="lookup",
            schema_name="new_AccountId", display_name="Account",
            target_entity="account",
        )
        assert info["kind"] == "OneToMany"
        assert calls["referenced_entity"] == "account"
        assert calls["referencing_entity"] == "new_widget"
        assert calls["lookup_schema"] == "new_AccountId"
        assert calls["lookup_display"] == "Account"

    def test_lookup_requires_target_entity(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="target-entity"):
            ma.add_attribute(
                backend, entity="new_widget", kind="lookup",
                schema_name="new_AccountId", display_name="Account",
            )


class TestAddAttributeImageFile:
    def test_image(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_photo", "Image")
            ma.add_attribute(
                backend, entity="new_widget", kind="image",
                schema_name="new_Photo", display_name="Photo",
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.ImageAttributeMetadata"

    def test_file_default_size(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_doc", "File")
            ma.add_attribute(
                backend, entity="new_widget", kind="file",
                schema_name="new_Doc", display_name="Doc",
            )
        body = _post_body(m)
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.FileAttributeMetadata"
        assert body["MaxSizeInKB"] == 32768

    def test_file_custom_size(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_doc", "File")
            ma.add_attribute(
                backend, entity="new_widget", kind="file",
                schema_name="new_Doc", display_name="Doc",
                max_size_kb=131072,
            )
        body = _post_body(m)
        assert body["MaxSizeInKB"] == 131072


class TestAddAttributeReadbackFail:
    def test_readback_fail_marks_lookup_error(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            attr_url = backend.url_for(
                f"EntityDefinitions(LogicalName='new_widget')/Attributes({_ATTR_ID})"
            )
            m.post(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')/Attributes"),
                status_code=204,
                headers={"OData-EntityId": attr_url},
            )
            m.get(
                backend.url_for(
                    "EntityDefinitions(LogicalName='new_widget')"
                    "/Attributes(LogicalName='new_label')"
                ),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.get(attr_url, status_code=500, json={"error": {"message": "boom"}})
            info = ma.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Label", display_name="Label",
                max_length=10,
            )
        assert info["created"] is True
        assert "attribute_lookup_error" in info


class TestAddAttributeDryRun:
    def test_dry_run_returns_envelope(self, profile, monkeypatch):
        monkeypatch.setenv("CRM_DRY_RUN", "1")
        backend = D365Backend(profile, password="pw", dry_run=True)
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            # The existence probe is a real GET even under dry-run.
            m.get(
                backend.url_for(
                    "EntityDefinitions(LogicalName='new_widget')"
                    "/Attributes(LogicalName='new_label')"
                ),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            info = ma.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Label", display_name="Label",
                max_length=10,
            )
        assert info.get("_dry_run") is True
