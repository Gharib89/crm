"""Unit tests for crm.core.metadata_attrs."""
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


_ATTR_ID = "33333333-3333-3333-3333-333333333333"


def _mock_post_and_readback(m, backend, entity: str, attr_logical: str,
                            attr_type: str = "String"):
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
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.StringAttributeMetadata"
        assert body["MaxLength"] == 100
        assert body["FormatName"]["Value"] == "Text"

    def test_string_requires_max_length(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="max_length"):
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
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.MemoAttributeMetadata"
        assert body["MaxLength"] == 4000

    def test_memo_requires_max_length(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="max_length"):
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
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.IntegerAttributeMetadata"
        assert body["MinValue"] == 0
        assert body["MaxValue"] == 1000

    def test_bigint(self, backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            _mock_post_and_readback(m, backend, "new_widget", "new_bignum", "BigInt")
            ma.add_attribute(
                backend, entity="new_widget", kind="bigint",
                schema_name="new_Bignum", display_name="Bignum",
            )
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
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
        body = m.request_history[0].json()
        assert body["Format"] == "DateOnly"

    def test_datetime_bad_format_rejected(self, backend):
        from crm.core import metadata_attrs as ma
        with pytest.raises(D365Error, match="format_name"):
            ma.add_attribute(
                backend, entity="new_widget", kind="datetime",
                schema_name="new_When", display_name="When",
                format_name="Garbage",
            )
