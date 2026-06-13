"""Unit + wire-level tests for the deepened OData response surface (#263).

Covers the four sibling verbs/functions added to the transport seam —
`get_collection`, `resolve_id_by_name`, `odata_literal`, `normalize_guid` —
plus the `_entity_id` key `_parse_response` now derives from `OData-EntityId`.
All HTTP is mocked via `requests_mock`; no live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import (
    D365Error,
    normalize_guid,
    odata_literal,
)


class TestOdataLiteral:
    def test_string_is_quoted(self):
        assert odata_literal("alice") == "'alice'"

    def test_embedded_single_quote_is_doubled(self):
        assert odata_literal("O'Brien") == "'O''Brien'"

    def test_bool_renders_lowercase_unquoted(self):
        assert odata_literal(True) == "true"
        assert odata_literal(False) == "false"

    def test_numbers_render_verbatim_unquoted(self):
        assert odata_literal(42) == "42"
        assert odata_literal(3.5) == "3.5"


class TestNormalizeGuid:
    CANON = "11111111-2222-3333-4444-555555555555"

    def test_hyphenated_any_case_canonicalizes_to_lowercase(self):
        assert normalize_guid(self.CANON.upper()) == self.CANON

    def test_bare_32_hex_gets_hyphens(self):
        assert normalize_guid(self.CANON.replace("-", "")) == self.CANON

    def test_brace_wrapped_accepted(self):
        assert normalize_guid("{" + self.CANON + "}") == self.CANON

    def test_paren_wrapped_accepted(self):
        assert normalize_guid("(" + self.CANON + ")") == self.CANON

    def test_surrounding_whitespace_tolerated(self):
        assert normalize_guid("  " + self.CANON + "  ") == self.CANON

    def test_non_guid_returns_none(self):
        assert normalize_guid("not-a-guid") is None
        assert normalize_guid("") is None


class TestEntityIdParsing:
    def test_entity_id_parsed_from_odata_entityid_header(self, backend, profile):
        guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with requests_mock.Mocker() as m:
            m.post(
                f"{profile.api_base}accounts",
                status_code=204,
                headers={"OData-EntityId": f"{profile.api_base}accounts({guid})"},
            )
            result = backend.post("accounts", json_body={"name": "x"})
        assert isinstance(result, dict)
        assert result["_entity_id"] == guid
        assert result["_entity_id_url"].endswith(f"accounts({guid})")

    def test_no_entity_id_key_when_header_absent(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.post(f"{profile.api_base}accounts", status_code=204)
            result = backend.post("accounts", json_body={"name": "x"})
        assert result is None


class TestGetCollection:
    def test_single_page_unwraps_value(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}accounts",
                json={"value": [{"accountid": "1"}, {"accountid": "2"}]},
            )
            rows = backend.get_collection("accounts")
        assert rows == [{"accountid": "1"}, {"accountid": "2"}]

    def test_follows_nextlink_to_exhaustion(self, backend, profile):
        page2 = f"{profile.api_base}accounts?$skiptoken=p2"
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}accounts",
                json={"value": [{"id": "1"}], "@odata.nextLink": page2},
            )
            m.get(page2, json={"value": [{"id": "2"}]})
            rows = backend.get_collection("accounts")
        assert [r["id"] for r in rows] == ["1", "2"]

    def test_max_pages_caps_follow(self, backend, profile):
        page2 = f"{profile.api_base}accounts?$skiptoken=p2"
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}accounts",
                json={"value": [{"id": "1"}], "@odata.nextLink": page2},
            )
            # page2 is registered but must NOT be fetched when max_pages=1.
            m.get(page2, json={"value": [{"id": "2"}]})
            rows = backend.get_collection("accounts", max_pages=1)
        assert [r["id"] for r in rows] == ["1"]

    def test_max_pages_below_one_rejected(self, backend):
        with pytest.raises(D365Error, match="max_pages"):
            backend.get_collection("accounts", max_pages=0)

    def test_admin_kwargs_forwarded(self, backend, profile):
        caller = "99999999-8888-7777-6666-555555555555"
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}accounts", json={"value": []})
            backend.get_collection("accounts", caller_id=caller)
            assert m.last_request.headers["MSCRMCallerID"] == caller


class TestResolveIdByName:
    def test_returns_first_row_id_when_found(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(
                f"{profile.api_base}webresourceset",
                json={"value": [{"webresourceid": "the-id"}]},
            )
            got = backend.resolve_id_by_name(
                "webresourceset",
                filter_field="name",
                id_field="webresourceid",
                value="new_script.js",
            )
        assert got == "the-id"

    def test_returns_none_when_absent(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}webresourceset", json={"value": []})
            got = backend.resolve_id_by_name(
                "webresourceset",
                filter_field="name",
                id_field="webresourceid",
                value="missing",
            )
        assert got is None

    def test_value_is_odata_escaped_in_filter(self, backend, profile):
        with requests_mock.Mocker() as m:
            m.get(f"{profile.api_base}webresourceset", json={"value": []})
            backend.resolve_id_by_name(
                "webresourceset",
                filter_field="name",
                id_field="webresourceid",
                value="O'Brien",
            )
            # requests_mock lowercases parsed query values; the doubled quote
            # (OData escaping) is the assertion that matters here.
            assert "o''brien" in m.last_request.qs["$filter"][0]
