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
