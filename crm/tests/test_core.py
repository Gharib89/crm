"""Unit tests for crm core modules.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import json
from typing import Any

import pytest
import requests_mock

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
)
from crm.core import (
    connection as conn_mod,
    entity as entity_mod,
    export as export_mod,
    metadata as meta_mod,
    query as query_mod,
    session as session_mod,
)


# ── connection.py ───────────────────────────────────────────────────────


class TestApiVersionNegotiation:
    """Issue #51: on-prem v9.x 501s on the v9.2 default — negotiate down to v9.1."""

    _BASE = "https://crm.contoso.local/contoso"
    _V92 = f"{_BASE}/api/data/v9.2/WhoAmI"
    _V91 = f"{_BASE}/api/data/v9.1/WhoAmI"
    _WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001"}

    def _backend(self):
        profile = ConnectionProfile(
            name="neg", url=self._BASE, domain="CONTOSO",
            username="alice", api_version="v9.2", verify_ssl=False,
        )
        return D365Backend(profile, password="pw")

    def test_negotiate_downgrades_to_v91_on_501(self):
        backend = self._backend()
        with requests_mock.Mocker() as m:
            m.get(self._V92, status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            m.get(self._V91, json=self._WHOAMI)
            info = conn_mod.test_connection(backend, negotiate=True)
        assert info["api_version"] == "v9.1"
        assert backend.profile.api_version == "v9.1"
        # exactly one downgrade: v9.2 probe then v9.1 probe = 2 requests
        assert len(m.request_history) == 2
        assert "/v9.2/" in m.request_history[0].url
        assert "/v9.1/" in m.request_history[1].url

    def test_no_downgrade_or_extra_probe_when_v92_succeeds(self):
        backend = self._backend()
        with requests_mock.Mocker() as m:
            m.get(self._V92, json=self._WHOAMI)
            info = conn_mod.test_connection(backend, negotiate=True)
        assert info["api_version"] == "v9.2"
        assert backend.profile.api_version == "v9.2"
        assert len(m.request_history) == 1  # no extra round-trip

    def test_explicit_version_not_auto_downgraded(self):
        # negotiate=False (explicit version): a 501 surfaces, no downgrade.
        backend = self._backend()
        with requests_mock.Mocker() as m:
            m.get(self._V92, status_code=501,
                  json={"error": {"code": "0x0", "message": "Not Implemented"}})
            with pytest.raises(D365Error) as ex:
                conn_mod.test_connection(backend, negotiate=False)
        assert ex.value.status == 501
        assert backend.profile.api_version == "v9.2"
        assert len(m.request_history) == 1

    def test_original_error_surfaced_when_downgrade_also_fails(self):
        backend = self._backend()
        with requests_mock.Mocker() as m:
            m.get(self._V92, status_code=501,
                  json={"error": {"code": "0x0", "message": "501 at v9.2"}})
            m.get(self._V91, status_code=500,
                  json={"error": {"code": "0x0", "message": "500 at v9.1"}})
            with pytest.raises(D365Error) as ex:
                conn_mod.test_connection(backend, negotiate=True)
        # the ORIGINAL 501 is surfaced, not the v9.1 500; version restored
        assert ex.value.status == 501
        assert "501 at v9.2" in str(ex.value)
        assert backend.profile.api_version == "v9.2"
        # no "During handling of the above exception" chaining of the v9.1 500
        assert ex.value.__suppress_context__ is True


# ── d365_backend.py ─────────────────────────────────────────────────────


class TestD365Backend:
    def test_url_for_relative_path(self, backend):
        url = backend.url_for("accounts")
        assert url == "https://crm.contoso.local/contoso/api/data/v9.2/accounts"

    def test_url_for_absolute_path_passthrough(self, backend):
        url = backend.url_for("https://other.local/api/data/v9.2/accounts")
        assert url == "https://other.local/api/data/v9.2/accounts"

    def test_request_sends_required_odata_headers(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("WhoAmI"),
                json={"UserId": "00000000-0000-0000-0000-000000000001"},
            )
            backend.get("WhoAmI")
        req = m.request_history[0]
        assert req.headers["OData-Version"] == "4.0"
        assert req.headers["OData-MaxVersion"] == "4.0"
        assert req.headers["Accept"] == "application/json"

    def test_request_dry_run_returns_preview(self, profile):
        b = D365Backend(profile, password="pw", dry_run=True)
        result = b.post("accounts", json_body={"name": "Foo"})
        assert isinstance(result, dict)
        assert result["_dry_run"] is True
        assert result["method"] == "POST"
        assert result["body"] == {"name": "Foo"}
        assert "accounts" in result["url"]

    def test_request_per_call_timeout_overrides_profile(self, backend):
        """A per-call timeout overrides profile.timeout for that request only."""
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("WhoAmI"),
                json={"UserId": "00000000-0000-0000-0000-000000000001"},
            )
            backend.get("WhoAmI", timeout=900)
            backend.get("WhoAmI")
        assert m.request_history[0].timeout == 900
        assert m.request_history[1].timeout == backend.profile.timeout

    def test_request_error_4xx_raises_d365error(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("accounts(00000000-0000-0000-0000-0000000000ff)"),
                status_code=404,
                json={"error": {"code": "0x80040217", "message": "Record Not Found"}},
            )
            with pytest.raises(D365Error) as ex:
                backend.get("accounts(00000000-0000-0000-0000-0000000000ff)")
        assert ex.value.status == 404
        assert ex.value.code == "0x80040217"
        assert "Record Not Found" in str(ex.value)


# ── entity.py ───────────────────────────────────────────────────────────


_GUID = "11111111-2222-3333-4444-555555555555"


class TestEntityCrud:
    def test_retrieve_builds_select_expand_params(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"accounts({_GUID})"),
                json={"accountid": _GUID, "name": "Contoso"},
            )
            result = entity_mod.retrieve(
                backend, "accounts", _GUID,
                select=["name", "telephone1"], expand=["primarycontactid"],
            )
        assert result["name"] == "Contoso"
        params = m.request_history[0].qs
        assert params["$select"] == ["name,telephone1"]
        assert params["$expand"] == ["primarycontactid"]

    def test_create_no_if_none_match_header(self, backend):
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("contacts"),
                json={"contactid": _GUID, "firstname": "Rafel"},
            )
            entity_mod.create(backend, "contacts", {"firstname": "Rafel"})
        req = m.request_history[0]
        assert "If-None-Match" not in req.headers
        assert req.headers["Prefer"] == "return=representation"
        assert json.loads(req.body) == {"firstname": "Rafel"}

    def test_update_sets_if_match_star_when_prevent_create(self, backend):
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"contacts({_GUID})"), status_code=204)
            entity_mod.update(backend, "contacts", _GUID, {"firstname": "R."})
        req = m.request_history[0]
        assert req.headers["If-Match"] == "*"

    def test_upsert_omits_if_match_header(self, backend):
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"contacts({_GUID})"), status_code=204)
            entity_mod.upsert(backend, "contacts", _GUID, {"firstname": "R."})
        req = m.request_history[0]
        assert "If-Match" not in req.headers

    def test_upsert_by_key_patches_alt_key_path_without_if_match(self, backend):
        with requests_mock.Mocker() as m:
            m.patch(
                backend.url_for("contacts(emailaddress1='joe%40x.com')"),
                status_code=204,
            )
            entity_mod.upsert_by_key(
                backend, "contacts", {"emailaddress1": "joe@x.com"},
                {"emailaddress1": "joe@x.com", "firstname": "Joe"},
            )
        req = m.request_history[0]
        assert req.method == "PATCH"
        assert "If-Match" not in req.headers
        # Per Dataverse guidance the alternate-key attribute is dropped from the
        # body (the server identifies the record from the URL key).
        assert json.loads(req.body) == {"firstname": "Joe"}

    def test_delete_returns_id_payload(self, backend):
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"contacts({_GUID})"), status_code=204)
            result = entity_mod.delete(backend, "contacts", _GUID)
        assert result["deleted"] is True
        # Normalized id key (ADR 0008 / #303): `_entity_id`, not bare `id`.
        assert result["_entity_id"] == _GUID
        assert result["_entity_id_url"].endswith(f"contacts({_GUID})")
        assert "id" not in result

    def test_invalid_guid_rejected(self, backend):
        with pytest.raises(D365Error, match="Invalid record id"):
            entity_mod.retrieve(backend, "contacts", "not-a-guid")


class TestAlternateKeyPath:
    def test_single_string_key_is_quoted(self):
        path = entity_mod.build_alternate_key_path(
            "contacts", {"emailaddress1": "joe@example.com"}
        )
        assert path == "contacts(emailaddress1='joe%40example.com')"

    def test_composite_key_comma_separated_in_order(self):
        path = entity_mod.build_alternate_key_path(
            "contacts", {"firstname": "Joe", "emailaddress1": "a@b.com"}
        )
        assert path == "contacts(firstname='Joe',emailaddress1='a%40b.com')"

    def test_numeric_and_bool_values_are_bare(self):
        path = entity_mod.build_alternate_key_path(
            "sample_things", {"sample_key1": 1, "sample_key2": True}
        )
        assert path == "sample_things(sample_key1=1,sample_key2=true)"

    def test_guid_value_is_bare_and_normalized(self):
        path = entity_mod.build_alternate_key_path(
            "accounts", {"_primarycontactid_value": "{" + _GUID.upper() + "}"}
        )
        assert path == f"accounts(_primarycontactid_value={_GUID})"

    def test_special_characters_are_url_escaped_and_quotes_doubled(self):
        path = entity_mod.build_alternate_key_path(
            "accounts", {"accountnumber": "A/B C&D'E"}
        )
        # single quote doubled per OData; '/', ' ', '&' percent-encoded; the
        # OData quote delimiters stay literal.
        assert path == "accounts(accountnumber='A%2FB%20C%26D''E')"

    def test_empty_key_values_rejected(self):
        with pytest.raises(D365Error, match="at least one key attribute"):
            entity_mod.build_alternate_key_path("accounts", {})


class TestResolveAlternateKey:
    """Validation of a named alternate key against entity metadata."""

    def _mock_meta(self, m, backend):
        m.get(backend.url_for("EntityDefinitions"), json={"value": [
            {"LogicalName": "account", "EntitySetName": "accounts",
             "PrimaryIdAttribute": "accountid", "PrimaryNameAttribute": "name"},
        ]})
        m.get(backend.url_for("EntityDefinitions(LogicalName='account')/Keys"),
              json={"value": [
                  {"LogicalName": "account_code_ak", "SchemaName": "Account_Code_AK",
                   "KeyAttributes": ["accountnumber"], "EntityKeyIndexStatus": "Active"},
                  {"LogicalName": "account_geo_ak", "SchemaName": "Account_Geo_AK",
                   "KeyAttributes": ["address1_city", "address1_country"],
                   "EntityKeyIndexStatus": "Active"},
              ]})

    def test_returns_matched_attributes_for_single_key(self, backend, isolated_home):
        with requests_mock.Mocker() as m:
            self._mock_meta(m, backend)
            matched = entity_mod.resolve_alternate_key(
                backend, "accounts", ["accountnumber"]
            )
        assert matched == ["accountnumber"]

    def test_matches_composite_key_regardless_of_order(self, backend, isolated_home):
        with requests_mock.Mocker() as m:
            self._mock_meta(m, backend)
            matched = entity_mod.resolve_alternate_key(
                backend, "accounts", ["address1_country", "address1_city"]
            )
        # Returned in the metadata's canonical order.
        assert matched == ["address1_city", "address1_country"]

    def test_unknown_key_raises_clear_error(self, backend, isolated_home):
        with requests_mock.Mocker() as m:
            self._mock_meta(m, backend)
            with pytest.raises(D365Error, match="No alternate key on 'accounts'"):
                entity_mod.resolve_alternate_key(backend, "accounts", ["notakey"])


# ── query.py ────────────────────────────────────────────────────────────


class TestQuery:
    def test_odata_query_compiles_filter_and_top(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("contacts"), json={"value": []})
            query_mod.odata_query(
                backend, "contacts",
                select=["fullname"],
                filter_="statecode eq 0",
                top=5,
                orderby="fullname desc",
            )
        req = m.request_history[0]
        assert req.qs["$select"] == ["fullname"]
        assert req.qs["$filter"] == ["statecode eq 0"]
        assert req.qs["$top"] == ["5"]
        assert req.qs["$orderby"] == ["fullname desc"]

    def test_odata_query_includes_annotations_prefer_header(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("contacts"), json={"value": []})
            query_mod.odata_query(backend, "contacts", include_annotations=True)
        assert (
            m.request_history[0].headers["Prefer"]
            == 'odata.include-annotations="*"'
        )

    def test_fetchxml_query_url_encodes_xml_once(self, backend):
        fx = "<fetch top='1'><entity name='account'><attribute name='name'/></entity></fetch>"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("accounts"), json={"value": [{"name": "Contoso"}]})
            result = query_mod.fetchxml_query(backend, "accounts", fx)
        assert result["value"][0]["name"] == "Contoso"
        req = m.request_history[0]
        assert "fetchXml=" in req.url
        assert "%3Cfetch" in req.url  # `<` encoded once

    def test_fetchxml_query_rejects_non_fetch_payload(self, backend):
        with pytest.raises(D365Error, match="<fetch>"):
            query_mod.fetchxml_query(backend, "accounts", "<not_fetch/>")

    def test_fetchxml_passes_params_dict(self, backend):
        fx = "<fetch top='1'><entity name='account'/></fetch>"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("accounts"), json={"value": []})
            query_mod.fetchxml_query(backend, "accounts", fx)
        req = m.request_history[0]
        # requests_mock lowercases qs keys
        assert req.qs["fetchxml"] == [fx]


# ── metadata.py ─────────────────────────────────────────────────────────


class TestMetadata:
    def test_list_entities_filters_custom_only(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions"),
                json={"value": [
                    {"LogicalName": "new_thing", "EntitySetName": "new_things",
                     "SchemaName": "new_Thing", "IsCustomEntity": True},
                ]},
            )
            items = meta_mod.list_entities(backend, custom_only=True)
        assert items[0]["LogicalName"] == "new_thing"
        assert m.request_history[0].qs["$filter"] == ["iscustomentity eq true"]

    def test_entity_info_uses_logical_name_path(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')"),
                json={"LogicalName": "account", "EntitySetName": "accounts"},
            )
            info = meta_mod.entity_info(backend, "account")
        assert info["EntitySetName"] == "accounts"

    def test_list_attributes_returns_value_array(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(
                    "EntityDefinitions(LogicalName='account')/Attributes"
                ),
                json={"value": [
                    {"LogicalName": "name", "SchemaName": "Name",
                     "AttributeType": "String", "IsCustomAttribute": False},
                ]},
            )
            attrs = meta_mod.list_attributes(backend, "account")
        assert attrs[0]["LogicalName"] == "name"

    def test_list_attributes_selects_validity_fields(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(
                    "EntityDefinitions(LogicalName='account')/Attributes"
                ),
                json={"value": []},
            )
            meta_mod.list_attributes(backend, "account")
            select = m.last_request.qs["$select"][0]
        for field in ("isvalidforcreate", "isvalidforupdate",
                      "isvalidforread", "requiredlevel"):
            assert field in select

    def test_list_attributes_flattens_required_level(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(
                    "EntityDefinitions(LogicalName='account')/Attributes"
                ),
                json={"value": [
                    {"LogicalName": "name", "SchemaName": "Name",
                     "AttributeType": "String", "IsCustomAttribute": False,
                     "IsValidForCreate": True, "IsValidForUpdate": True,
                     "IsValidForRead": True,
                     "RequiredLevel": {"Value": "ApplicationRequired"}},
                    {"LogicalName": "createdon", "SchemaName": "CreatedOn",
                     "AttributeType": "DateTime", "IsCustomAttribute": False,
                     "IsValidForCreate": False, "IsValidForUpdate": False,
                     "IsValidForRead": True, "RequiredLevel": {"Value": "None"}},
                ]},
            )
            attrs = meta_mod.list_attributes(backend, "account")
        assert attrs[0]["RequiredLevel"] == "ApplicationRequired"
        assert attrs[0]["IsValidForCreate"] is True
        assert attrs[1]["RequiredLevel"] == "None"
        assert attrs[1]["IsValidForCreate"] is False


# ── session.py ──────────────────────────────────────────────────────────


class TestSessionStore:
    def test_save_then_load_profile_roundtrip(self, isolated_home, profile):
        session_mod.save_profile(profile)
        loaded = session_mod.load_profile(profile.name)
        assert loaded.url == profile.url
        assert loaded.username == profile.username
        assert loaded.api_version == profile.api_version

    def test_list_profiles_alphabetical(self, isolated_home, profile):
        for n in ["beta", "alpha", "gamma"]:
            p = ConnectionProfile(
                name=n, url="https://x/y", domain="", username="u",
                api_version="v9.2", verify_ssl=True,
            )
            session_mod.save_profile(p)
        assert session_mod.list_profiles() == ["alpha", "beta", "gamma"]

    def test_session_history_trims_to_max_length(self, isolated_home):
        state = session_mod.load_session("trim")
        for i in range(20):
            session_mod.append_history(state, f"cmd-{i}", max_len=5)
        assert state["history"] == [f"cmd-{i}" for i in range(15, 20)]

    def test_atomic_write_replaces_file(self, isolated_home):
        target = session_mod.session_path("x")
        session_mod.save_session({"name": "x", "v": 1}, "x")
        first = target.read_text()
        session_mod.save_session({"name": "x", "v": 2}, "x")
        second = target.read_text()
        assert first != second
        assert json.loads(second)["v"] == 2


# ── export.py ───────────────────────────────────────────────────────────


class TestAssociate:
    def test_associate_posts_odata_id_to_ref(self, backend):
        with requests_mock.Mocker() as m:
            target_url = backend.url_for(f"accounts({_GUID})/contact_customer_accounts/$ref")
            m.post(target_url, status_code=204)
            other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            entity_mod.associate(
                backend, "accounts", _GUID,
                "contact_customer_accounts", "contacts", other,
            )
        body = json.loads(m.request_history[0].body)
        assert body["@odata.id"].endswith(f"contacts({other})")

    def test_disassociate_collection_uses_id_param(self, backend):
        other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with requests_mock.Mocker() as m:
            m.delete(requests_mock.ANY, status_code=204)
            entity_mod.disassociate(
                backend, "accounts", _GUID,
                "contact_customer_accounts",
                related_set="contacts", related_id=other,
            )
        url = m.request_history[0].url
        assert "/contact_customer_accounts/$ref" in url
        assert "%24id=" in url or "$id=" in url

    def test_disassociate_single_valued_omits_id(self, backend):
        with requests_mock.Mocker() as m:
            m.delete(
                backend.url_for(f"contacts({_GUID})/parentcustomerid_account/$ref"),
                status_code=204,
            )
            entity_mod.disassociate(
                backend, "contacts", _GUID, "parentcustomerid_account",
            )
        assert "$id=" not in m.request_history[0].url

    def test_set_lookup_patches_odata_bind(self, backend):
        other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"contacts({_GUID})"), status_code=204)
            entity_mod.set_lookup(
                backend, "contacts", _GUID,
                "parentcustomerid_account", "accounts", other,
            )
        body = json.loads(m.request_history[0].body)
        assert body["parentcustomerid_account@odata.bind"] == f"/accounts({other})"


class TestSavedAndUserQuery:
    def test_saved_query_sends_savedquery_param(self, backend):
        qid = "00000000-0000-0000-00aa-000010001002"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("accounts"), json={"value": []})
            query_mod.saved_query(backend, "accounts", qid)
        assert m.request_history[0].qs["savedquery"] == [qid]

    def test_user_query_sends_userquery_param(self, backend):
        qid = "11111111-2222-3333-4444-555555555555"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("contacts"), json={"value": []})
            query_mod.user_query(backend, "contacts", qid)
        assert m.request_history[0].qs["userquery"] == [qid]


class TestPicklistMetadata:
    def test_picklist_options_casts_and_expands(self, backend):
        from crm.core import metadata as meta_mod_local
        attr_info_url = backend.url_for(
            "EntityDefinitions(LogicalName='account')/Attributes(LogicalName='industrycode')"
        )
        cast_url = backend.url_for(
            "EntityDefinitions(LogicalName='account')/Attributes(LogicalName='industrycode')"
            "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        )
        with requests_mock.Mocker() as m:
            m.get(attr_info_url, json={"LogicalName": "industrycode", "AttributeType": "Picklist"})
            m.get(cast_url, json={
                "LogicalName": "industrycode",
                "OptionSet": {"Options": [
                    {"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Accounting"}}},
                    {"Value": 2, "Label": {"UserLocalizedLabel": {"Label": "Agri"}}},
                ]},
            })
            info = meta_mod_local.picklist_options(backend, "account", "industrycode")
        assert info["LogicalName"] == "industrycode"
        assert info["OptionSet"]["Options"][0]["Value"] == 1
        # Second request is the typed cast GET; first is the attribute-type probe.
        cast_req = m.request_history[1]
        assert "PicklistAttributeMetadata" in cast_req.url
        assert "%24expand=OptionSet" in cast_req.url or "$expand=OptionSet" in cast_req.url


class TestCreateEntity:
    def test_create_entity_posts_expected_payload(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_project')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                headers={"OData-EntityId":
                         backend.url_for("EntityDefinitions(11111111-1111-1111-1111-111111111111)")},
            )
            m.get(
                backend.url_for("EntityDefinitions(11111111-1111-1111-1111-111111111111)"),
                json={"LogicalName": "new_project", "EntitySetName": "new_projects"},
            )
            info = meta_mod_local.create_entity(
                backend,
                schema_name="new_Project",
                display_name="Project",
                description="Test entity",
                solution="MyDevSolution",
            )
        assert info["created"] is True
        assert info["schema_name"] == "new_Project"
        assert info["logical_name"] == "new_project"
        assert info["primary_attribute"] == "new_name"
        assert info["solution"] == "MyDevSolution"
        assert "metadata_id_url" in info

        req = next(r for r in m.request_history if r.method == "POST")
        assert req.method == "POST"
        assert req.headers.get("MSCRM.SolutionUniqueName") == "MyDevSolution"
        body = req.json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.EntityMetadata"
        assert body["SchemaName"] == "new_Project"
        assert body["LogicalName"] == "new_project"
        assert body["OwnershipType"] == "UserOwned"
        assert body["DisplayName"]["LocalizedLabels"][0]["Label"] == "Project"
        assert body["DisplayCollectionName"]["LocalizedLabels"][0]["Label"] == "Projects"
        prim = body["Attributes"][0]
        assert prim["@odata.type"] == "Microsoft.Dynamics.CRM.StringAttributeMetadata"
        assert prim["SchemaName"] == "new_Name"
        assert prim["IsPrimaryName"] is True
        assert prim["MaxLength"] == 200
        assert body["Description"]["LocalizedLabels"][0]["Label"] == "Test entity"

    def test_create_entity_rejects_schema_without_prefix(self, backend):
        from crm.core import metadata as meta_mod_local
        with pytest.raises(D365Error, match="publisher prefix"):
            meta_mod_local.create_entity(
                backend, schema_name="Project", display_name="Project",
            )


class TestCreateEntityReadback:
    _MD_ID = "11111111-1111-1111-1111-111111111111"

    def test_create_entity_returns_server_entity_set_name(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
            md_url = backend.url_for(f"EntityDefinitions({self._MD_ID})")
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_city')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                headers={"OData-EntityId": md_url},
            )
            m.get(
                md_url,
                json={"LogicalName": "new_city", "EntitySetName": "new_cities"},
            )
            info = meta_mod_local.create_entity(
                backend, schema_name="new_City", display_name="City",
            )
        assert info["created"] is True
        assert info["entity_set_name"] == "new_cities"
        assert info["metadata_id_url"] == md_url

    def test_create_entity_partial_when_readback_fails(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
            md_url = backend.url_for(f"EntityDefinitions({self._MD_ID})")
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_city')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                headers={"OData-EntityId": md_url},
            )
            m.get(
                md_url,
                status_code=500,
                json={"error": {"code": "0x...", "message": "boom"}},
            )
            info = meta_mod_local.create_entity(
                backend, schema_name="new_City", display_name="City",
            )
        assert info["created"] is True
        assert info["entity_set_name"] is None
        assert "entity_set_lookup_error" in info
        assert info["metadata_id_url"] == md_url

    def test_create_entity_partial_when_odata_entityid_header_missing(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_city')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            m.post(
                backend.url_for("EntityDefinitions"),
                status_code=204,
                # No OData-EntityId header set
            )
            info = meta_mod_local.create_entity(
                backend, schema_name="new_City", display_name="City",
            )
        assert info["created"] is True
        assert info["entity_set_name"] is None
        assert info["metadata_id_url"] is None
        assert "entity_set_lookup_error" in info


class TestPublish:
    def test_publish_all_posts(self, backend):
        from crm.core import solution as sol_mod_local
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            result = sol_mod_local.publish_all(backend)
        assert result["published"] is True
        assert m.request_history[0].method == "POST"

    def test_publish_xml_sends_parameterxml(self, backend):
        from crm.core import solution as sol_mod_local
        xml = "<importexportxml><entities><entity>account</entity></entities></importexportxml>"
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("PublishXml"), status_code=204)
            sol_mod_local.publish_xml(backend, xml)
        body = json.loads(m.request_history[0].body)
        assert body["ParameterXml"] == xml


class TestOrderedKeys:
    def test_ordered_keys_drops_lookups_and_annotations(self):
        from crm.core.export import _ordered_keys
        records = [
            {
                "name": "Contoso",
                "_owner_value": "guid-1",
                "@odata.etag": "W/\"123\"",
                "createdon": "2026-01-01",
            },
            {"name": "Initech", "_modifiedby_value": "guid-2"},
        ]
        assert _ordered_keys(records) == ["name", "createdon"]


class TestExport:
    def test_export_records_csv(self, backend, tmp_path):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("contacts"),
                json={"value": [
                    {"fullname": "Alice", "telephone1": "+1-555-0001"},
                    {"fullname": "Bob",   "telephone1": "+1-555-0002"},
                ]},
            )
            out = tmp_path / "contacts.csv"
            info = export_mod.export_records(
                backend, "contacts", out,
                select=["fullname", "telephone1"],
            )
        assert info["count"] == 2
        text = out.read_text()
        assert "fullname,telephone1" in text.splitlines()[0]
        assert "Alice" in text and "Bob" in text


class TestWorkflow:
    def test_list_workflows_filters_definitions(self, backend):
        from crm.core import workflow as wf_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("workflows"),
                json={"value": [
                    {"workflowid": "w1", "name": "Auto-set Owner", "category": 0,
                     "primaryentity": "contact", "type": 1,
                     "statecode": 1, "statuscode": 2, "ondemand": True},
                ]},
            )
            items = wf_mod.list_workflows(
                backend,
                category=0,
                primary_entity="contact",
                activated_only=True,
                on_demand_only=True,
            )
        assert len(items) == 1
        assert items[0]["workflowid"] == "w1"
        req = m.request_history[0]
        flt = req.qs["$filter"][0]
        assert "type eq 1" in flt
        assert "category eq 0" in flt
        assert "primaryentity eq 'contact'" in flt
        assert "statecode eq 1" in flt
        assert "ondemand eq true" in flt

    def test_set_workflow_state_activates(self, backend):
        from crm.core import workflow as wf_mod
        wid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"workflows({wid})"), status_code=204)
            info = wf_mod.set_workflow_state(backend, wid, activate=True)
        assert info["activated"] is True
        assert info["statecode"] == 1 and info["statuscode"] == 2
        req = m.request_history[0]
        body = json.loads(req.body)
        assert body == {"statecode": 1, "statuscode": 2}
        assert req.headers.get("If-Match") == "*"

    def test_set_workflow_state_deactivates(self, backend):
        from crm.core import workflow as wf_mod
        wid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"workflows({wid})"), status_code=204)
            info = wf_mod.set_workflow_state(backend, wid, activate=False)
        assert info["activated"] is False
        body = json.loads(m.request_history[0].body)
        assert body == {"statecode": 0, "statuscode": 1}

    def test_set_workflow_state_draft_id_no_extra_requests(self, backend):
        """A definition id succeeds directly: one PATCH, no resolve GET, no redirect."""
        from crm.core import workflow as wf_mod
        wid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"workflows({wid})"), status_code=204)
            info = wf_mod.set_workflow_state(backend, wid, activate=True)
        assert info["workflow_id"] == wid
        assert info["resolved_from_activation_id"] is None
        assert [r.method for r in m.request_history] == ["PATCH"]

    def test_set_workflow_state_resolves_activation_parent(self, backend):
        """An activation-record id (0x80045003) is resolved to its parent and the
        PATCH retried against it, preserving body and admin headers."""
        from crm.core import workflow as wf_mod
        wid = "22222222-2222-2222-2222-222222222222"
        parent_guid = "33333333-3333-3333-3333-333333333333"
        caller = "44444444-4444-4444-4444-444444444444"
        error_body = {"error": {"code": "0x80045003", "message": "Cannot update a workflow activation."}}
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"workflows({wid})"), status_code=400, json=error_body)
            m.get(
                backend.url_for(f"workflows({wid})"),
                json={"_parentworkflowid_value": parent_guid},
            )
            m.patch(backend.url_for(f"workflows({parent_guid})"), status_code=204)
            info = wf_mod.set_workflow_state(
                backend, wid, activate=False, caller_id=caller)
        assert info["workflow_id"] == parent_guid
        assert info["resolved_from_activation_id"] == wid
        assert info["activated"] is False
        assert [r.method for r in m.request_history] == ["PATCH", "GET", "PATCH"]
        # The resolve GET runs under the same impersonation as the state change.
        assert m.request_history[1].headers.get("MSCRMCallerID") == caller
        retried = m.request_history[2]
        assert json.loads(retried.body) == {"statecode": 0, "statuscode": 1}
        assert retried.headers.get("MSCRMCallerID") == caller
        assert retried.headers.get("If-Match") == "*"

    def test_set_workflow_state_reraises_when_parent_unresolvable(self, backend):
        """No parent on the row → the original 0x80045003 surfaces, no retry."""
        from crm.core import workflow as wf_mod
        wid = "22222222-2222-2222-2222-222222222222"
        error_body = {"error": {"code": "0x80045003", "message": "Cannot update a workflow activation."}}
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"workflows({wid})"), status_code=400, json=error_body)
            m.get(backend.url_for(f"workflows({wid})"), json={})
            with pytest.raises(D365Error) as exc_info:
                wf_mod.set_workflow_state(backend, wid, activate=True)
        assert exc_info.value.code == "0x80045003"
        assert [r.method for r in m.request_history] == ["PATCH", "GET"]

    def test_set_workflow_state_auto_resolve_opt_out(self, backend):
        """auto_resolve_parent=False raises immediately with no resolve GET."""
        from crm.core import workflow as wf_mod
        wid = "22222222-2222-2222-2222-222222222222"
        error_body = {"error": {"code": "0x80045003", "message": "Cannot update a workflow activation."}}
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"workflows({wid})"), status_code=400, json=error_body)
            with pytest.raises(D365Error) as exc_info:
                wf_mod.set_workflow_state(
                    backend, wid, activate=True, auto_resolve_parent=False)
        assert exc_info.value.code == "0x80045003"
        assert [r.method for r in m.request_history] == ["PATCH"]

    def test_set_workflow_state_dry_run_resolves_proactively(self, profile):
        """Dry-run issues a real resolve GET (the short-circuited PATCH can never
        raise 0x80045003) so the preview keys on the parent the live run would PATCH."""
        from crm.core import workflow as wf_mod
        wid = "22222222-2222-2222-2222-222222222222"
        parent_guid = "33333333-3333-3333-3333-333333333333"
        backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({wid})"),
                json={"_parentworkflowid_value": parent_guid},
            )
            info = wf_mod.set_workflow_state(backend, wid, activate=False)
        assert backend.dry_run is True
        assert info["workflow_id"] == parent_guid
        assert info["resolved_from_activation_id"] == wid
        # The GET is the only real request; the PATCH stays a dry-run preview.
        assert [r.method for r in m.request_history] == ["GET"]

    def test_set_workflow_state_dry_run_draft_id_unchanged(self, profile):
        """Dry-run with a definition id previews against the passed GUID."""
        from crm.core import workflow as wf_mod
        wid = "11111111-1111-1111-1111-111111111111"
        backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({wid})"), json={})
            info = wf_mod.set_workflow_state(backend, wid, activate=True)
        assert backend.dry_run is True
        assert info["workflow_id"] == wid
        assert info["resolved_from_activation_id"] is None
        assert [r.method for r in m.request_history] == ["GET"]

    def test_execute_workflow_posts_bound_action(self, backend):
        from crm.core import workflow as wf_mod
        wid = "aaaa1111-1111-1111-1111-111111111111"
        tid = "bbbb2222-2222-2222-2222-222222222222"
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for(
                    f"workflows({wid})/Microsoft.Dynamics.CRM.ExecuteWorkflow"
                ),
                json={"Id": "cccc3333-3333-3333-3333-333333333333"},
            )
            info = wf_mod.execute_workflow(backend, wid, tid)
        assert info["workflow_id"] == wid
        assert info["target_id"] == tid
        assert info["async_operation_id"] == "cccc3333-3333-3333-3333-333333333333"
        body = json.loads(m.request_history[0].body)
        assert body == {"EntityId": tid}

    def test_execute_workflow_requires_both_ids(self, backend):
        from crm.core import workflow as wf_mod
        with pytest.raises(D365Error):
            wf_mod.execute_workflow(backend, "", "x")
        with pytest.raises(D365Error):
            wf_mod.execute_workflow(backend, "x", "")

    def test_activation_record_hint_resolves_parent(self, backend):
        from crm.core import workflow as wf_mod
        wid = "22222222-2222-2222-2222-222222222222"
        parent_guid = "33333333-3333-3333-3333-333333333333"
        error_body = {"error": {"code": "0x80045003", "message": "Cannot update a workflow activation."}}
        with requests_mock.Mocker() as m:
            m.patch(backend.url_for(f"workflows({wid})"), status_code=400, json=error_body)
            # Auto-resolve attempts a parent GET; yield no parent so the
            # original rejection surfaces and the hint path is exercised.
            m.get(backend.url_for(f"workflows({wid})"), json={})
            with pytest.raises(D365Error) as exc_info:
                wf_mod.set_workflow_state(backend, wid, activate=True)
        exc = exc_info.value
        assert exc.code == "0x80045003"

        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({wid})"),
                json={"_parentworkflowid_value": parent_guid},
            )
            hint = wf_mod.activation_record_hint(backend, wid, exc)
        assert hint is not None
        assert parent_guid in hint
        # Verify $select=parentworkflowid was used
        req = m.request_history[0]
        assert req.qs.get("$select") == ["parentworkflowid"]

    def test_activation_record_hint_fallback_when_no_parent(self, backend):
        from crm.core import workflow as wf_mod
        wid = "44444444-4444-4444-4444-444444444444"
        exc = D365Error("Cannot update a workflow activation.", status=400, code="0x80045003")

        # GET returns empty dict — no parent
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({wid})"), json={})
            hint = wf_mod.activation_record_hint(backend, wid, exc)
        assert hint is not None
        assert "activation record" in hint
        # No parent GUID in hint
        assert "33333333" not in hint

        # GET returns 500 — should fall back gracefully
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({wid})"), status_code=500, json={"error": {"code": "0x80000000", "message": "Server error"}})
            hint2 = wf_mod.activation_record_hint(backend, wid, exc)
        assert hint2 is not None
        assert "activation record" in hint2

    def test_activation_record_hint_ignores_other_codes(self, backend):
        from crm.core import workflow as wf_mod
        wid = "55555555-5555-5555-5555-555555555555"
        exc = D365Error("Some other error.", status=400, code="0x80040217")

        with requests_mock.Mocker() as m:
            result = wf_mod.activation_record_hint(backend, wid, exc)
        assert result is None
        assert len(m.request_history) == 0

    def test_activation_delete_hint_resolves_parent(self, backend):
        from crm.core import workflow as wf_mod
        wid = "22222222-2222-2222-2222-222222222222"
        parent_guid = "33333333-3333-3333-3333-333333333333"
        exc = D365Error("Cannot delete a workflow activation.", status=400, code="0x80045004")

        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({wid})"),
                json={"_parentworkflowid_value": parent_guid},
            )
            hint = wf_mod.activation_delete_hint(backend, wid, exc)
        assert hint is not None
        assert parent_guid in hint
        assert f"crm workflow deactivate {parent_guid}" in hint
        # Verify $select=parentworkflowid was used (lowercase nav-prop per $metadata)
        req = m.request_history[0]
        assert req.qs.get("$select") == ["parentworkflowid"]

    def test_activation_delete_hint_fallback_when_no_parent(self, backend):
        from crm.core import workflow as wf_mod
        wid = "44444444-4444-4444-4444-444444444444"
        exc = D365Error("Cannot delete a workflow activation.", status=400, code="0x80045004")

        # GET returns empty dict — no parent
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({wid})"), json={})
            hint = wf_mod.activation_delete_hint(backend, wid, exc)
        assert hint is not None
        assert "parentworkflowid" in hint
        # No concrete parent GUID in the static fallback
        assert "33333333" not in hint
        # Original error untouched
        assert exc.code == "0x80045004"
        assert str(exc) == "Cannot delete a workflow activation."

        # GET returns 500 — should fall back gracefully
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({wid})"), status_code=500, json={"error": {"code": "0x80000000", "message": "Server error"}})
            hint2 = wf_mod.activation_delete_hint(backend, wid, exc)
        assert hint2 is not None
        assert "parentworkflowid" in hint2

    def test_activation_delete_hint_ignores_other_codes(self, backend):
        from crm.core import workflow as wf_mod
        wid = "55555555-5555-5555-5555-555555555555"
        exc = D365Error("Some other error.", status=400, code="0x80040217")

        with requests_mock.Mocker() as m:
            result = wf_mod.activation_delete_hint(backend, wid, exc)
        assert result is None
        assert len(m.request_history) == 0


class TestWorkflowDelete:
    """`delete_workflow` — deactivate-then-delete resolving activation records
    to their definition (issue #164)."""

    _DEF_ID = "11111111-1111-1111-1111-111111111111"

    def test_delete_draft_definition_directly(self, backend):
        """A draft definition (type=1, statecode=0) is deleted with no state change."""
        from crm.core import workflow as wf_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({self._DEF_ID})"),
                json={"workflowid": self._DEF_ID, "name": "Auto-set Owner",
                      "type": 1, "statecode": 0},
            )
            m.delete(backend.url_for(f"workflows({self._DEF_ID})"), status_code=204)
            info = wf_mod.delete_workflow(backend, self._DEF_ID)
        assert info["deleted"] is True
        assert info["workflow_id"] == self._DEF_ID
        assert info["name"] == "Auto-set Owner"
        assert info["deactivated"] is False
        assert info["resolved_from_activation_id"] is None
        assert [r.method for r in m.request_history] == ["GET", "DELETE"]

    def test_delete_active_definition_deactivates_first(self, backend):
        """An active definition is deactivated (statecode=0) before the DELETE,
        under the caller's impersonation."""
        from crm.core import workflow as wf_mod
        caller = "44444444-4444-4444-4444-444444444444"
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({self._DEF_ID})"),
                json={"workflowid": self._DEF_ID, "name": "Auto-set Owner",
                      "type": 1, "statecode": 1},
            )
            m.patch(backend.url_for(f"workflows({self._DEF_ID})"), status_code=204)
            m.delete(backend.url_for(f"workflows({self._DEF_ID})"), status_code=204)
            info = wf_mod.delete_workflow(backend, self._DEF_ID, caller_id=caller)
        assert info["deleted"] is True
        assert info["deactivated"] is True
        assert [r.method for r in m.request_history] == ["GET", "PATCH", "DELETE"]
        patch_req = m.request_history[1]
        assert json.loads(patch_req.body) == {"statecode": 0, "statuscode": 1}
        assert patch_req.headers.get("MSCRMCallerID") == caller
        assert m.request_history[2].headers.get("MSCRMCallerID") == caller

    def test_delete_activation_record_operates_on_parent_definition(self, backend):
        """A type=2 activation-record GUID resolves via parentworkflowid to the
        live parent definition; deactivate+delete run against the parent."""
        from crm.core import workflow as wf_mod
        act_id = "22222222-2222-2222-2222-222222222222"
        parent_guid = "33333333-3333-3333-3333-333333333333"
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({act_id})"),
                json={"workflowid": act_id, "name": "Auto-set Owner",
                      "type": 2, "statecode": 1,
                      "_parentworkflowid_value": parent_guid},
            )
            m.get(
                backend.url_for(f"workflows({parent_guid})"),
                json={"workflowid": parent_guid, "name": "Auto-set Owner",
                      "type": 1, "statecode": 1},
            )
            m.patch(backend.url_for(f"workflows({parent_guid})"), status_code=204)
            m.delete(backend.url_for(f"workflows({parent_guid})"), status_code=204)
            info = wf_mod.delete_workflow(backend, act_id)
        assert info["deleted"] is True
        assert info["workflow_id"] == parent_guid
        assert info["resolved_from_activation_id"] == act_id
        assert info["deactivated"] is True
        assert [r.method for r in m.request_history] == ["GET", "GET", "PATCH", "DELETE"]

    def test_delete_activation_record_without_live_parent_fails_clean(self, backend):
        """No supported Web API path when the parent definition is gone (ADR 0003):
        null parentworkflowid or a dangling parent GUID both fail with a clean
        operational error before any mutation."""
        from crm.core import workflow as wf_mod
        act_id = "22222222-2222-2222-2222-222222222222"
        parent_guid = "33333333-3333-3333-3333-333333333333"

        # Null parent.
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({act_id})"),
                json={"workflowid": act_id, "type": 2, "statecode": 1,
                      "_parentworkflowid_value": None},
            )
            with pytest.raises(D365Error) as exc_info:
                wf_mod.delete_workflow(backend, act_id)
        assert "no live parent definition" in str(exc_info.value)
        assert "D365 UI" in str(exc_info.value)
        assert [r.method for r in m.request_history] == ["GET"]

        # Dangling parent (404).
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({act_id})"),
                json={"workflowid": act_id, "type": 2, "statecode": 1,
                      "_parentworkflowid_value": parent_guid},
            )
            m.get(
                backend.url_for(f"workflows({parent_guid})"),
                status_code=404,
                json={"error": {"code": "0x80040217", "message": "Does Not Exist"}},
            )
            with pytest.raises(D365Error) as exc_info:
                wf_mod.delete_workflow(backend, act_id)
        assert "no live parent definition" in str(exc_info.value)
        assert [r.method for r in m.request_history] == ["GET", "GET"]

    def test_delete_failure_after_deactivate_reports_draft_no_rollback(self, backend):
        """Deactivate lands, DELETE fails: no rollback — the error states the
        definition was deactivated and remains a draft, keeping the server code."""
        from crm.core import workflow as wf_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({self._DEF_ID})"),
                json={"workflowid": self._DEF_ID, "name": "Auto-set Owner",
                      "type": 1, "statecode": 1},
            )
            m.patch(backend.url_for(f"workflows({self._DEF_ID})"), status_code=204)
            m.delete(
                backend.url_for(f"workflows({self._DEF_ID})"),
                status_code=400,
                json={"error": {"code": "0x80048d19", "message": "Delete blocked."}},
            )
            with pytest.raises(D365Error) as exc_info:
                wf_mod.delete_workflow(backend, self._DEF_ID)
        exc = exc_info.value
        assert "deactivated" in str(exc)
        assert "draft" in str(exc)
        assert exc.code == "0x80048d19"
        assert exc.status == 400
        # PATCH ran, DELETE failed, nothing after (no rollback re-activate).
        assert [r.method for r in m.request_history] == ["GET", "PATCH", "DELETE"]

    def test_delete_failure_without_deactivate_propagates_unchanged(self, backend):
        """A failed DELETE on an already-draft definition surfaces as-is."""
        from crm.core import workflow as wf_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({self._DEF_ID})"),
                json={"workflowid": self._DEF_ID, "name": "Auto-set Owner",
                      "type": 1, "statecode": 0},
            )
            m.delete(
                backend.url_for(f"workflows({self._DEF_ID})"),
                status_code=400,
                json={"error": {"code": "0x80048d19", "message": "Delete blocked."}},
            )
            with pytest.raises(D365Error) as exc_info:
                wf_mod.delete_workflow(backend, self._DEF_ID)
        assert "deactivated" not in str(exc_info.value)
        assert exc_info.value.code == "0x80048d19"

    def test_delete_dry_run_previews_with_live_resolve(self, profile):
        """Dry-run issues real resolve GETs (the reads-execute rule) and returns
        a preview envelope — no PATCH, no DELETE."""
        from crm.core import workflow as wf_mod
        act_id = "22222222-2222-2222-2222-222222222222"
        parent_guid = "33333333-3333-3333-3333-333333333333"
        backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({act_id})"),
                json={"workflowid": act_id, "name": "Auto-set Owner",
                      "type": 2, "statecode": 1,
                      "_parentworkflowid_value": parent_guid},
            )
            m.get(
                backend.url_for(f"workflows({parent_guid})"),
                json={"workflowid": parent_guid, "name": "Auto-set Owner",
                      "type": 1, "statecode": 1},
            )
            info = wf_mod.delete_workflow(backend, act_id)
        assert backend.dry_run is True
        assert info["_dry_run"] is True
        assert info["would_delete"] == parent_guid
        assert info["would_deactivate"] is True
        assert info["workflow_id"] == parent_guid
        assert info["resolved_from_activation_id"] == act_id
        assert "deleted" not in info
        # Only the resolve GETs hit the wire.
        assert [r.method for r in m.request_history] == ["GET", "GET"]

    def test_delete_dry_run_draft_definition(self, profile):
        """Dry-run on a draft definition previews would_deactivate=False."""
        from crm.core import workflow as wf_mod
        backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for(f"workflows({self._DEF_ID})"),
                json={"workflowid": self._DEF_ID, "name": "Auto-set Owner",
                      "type": 1, "statecode": 0},
            )
            info = wf_mod.delete_workflow(backend, self._DEF_ID)
        assert backend.dry_run is True
        assert info["_dry_run"] is True
        assert info["would_delete"] == self._DEF_ID
        assert info["would_deactivate"] is False
        assert info["resolved_from_activation_id"] is None
        assert [r.method for r in m.request_history] == ["GET"]


class TestCountEntitySet:
    def test_count_returns_int_from_text_plain(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("contacts/$count"),
                text="42",
                headers={"Content-Type": "text/plain"},
            )
            result = query_mod.count_entity_set(backend, "contacts")
        assert result == 42
        assert len(m.request_history) == 1, "happy path must issue exactly one request"

    def test_count_falls_back_when_text_plain_empty(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("contacts/$count"),
                text="",
                headers={"Content-Type": "text/plain"},
            )
            m.get(
                backend.url_for("contacts"),
                json={"value": [{"contactid": "x"}], "@odata.count": 7},
            )
            result = query_mod.count_entity_set(backend, "contacts")
        assert result == 7
        assert len(m.request_history) == 2, "fallback must issue two requests in order"
        assert m.request_history[0].url.endswith("/$count")
        assert "$count=true" in m.request_history[1].url or "%24count=true" in m.request_history[1].url


# ── cli.py ──────────────────────────────────────────────────────────────


class TestReplBackendCache:
    def test_repl_reuses_backend_across_commands(self, monkeypatch, profile):
        from crm.cli import CLIContext
        from crm.utils import d365_backend as backend_mod
        from crm.core.connection import ResolvedCredentials
        ctx = CLIContext()
        ctx.password = "pw"
        # Stub credential resolution to avoid env/profile loading.
        monkeypatch.setattr(
            "crm.core.connection.resolve_credentials",
            lambda profile_name=None, password_override=None, *, allow_prompt=False:
                ResolvedCredentials(profile=profile, password="pw"),
        )
        # Count D365Backend instantiations.
        calls = {"n": 0}
        real_init = backend_mod.D365Backend.__init__
        def counting_init(self, *a, **kw):
            calls["n"] += 1
            real_init(self, *a, **kw)
        monkeypatch.setattr(backend_mod.D365Backend, "__init__", counting_init)
        b1 = ctx.backend()
        b2 = ctx.backend()
        assert b1 is b2
        assert calls["n"] == 1

    def test_repl_backend_invalidated_on_connect(self, monkeypatch, profile):
        from crm.cli import CLIContext
        from crm.utils import d365_backend as backend_mod
        from crm.core.connection import ResolvedCredentials
        ctx = CLIContext()
        ctx.password = "pw"
        monkeypatch.setattr(
            "crm.core.connection.resolve_credentials",
            lambda profile_name=None, password_override=None, *, allow_prompt=False:
                ResolvedCredentials(profile=profile, password="pw"),
        )
        calls = {"n": 0}
        real_init = backend_mod.D365Backend.__init__
        def counting_init(self, *a, **kw):
            calls["n"] += 1
            real_init(self, *a, **kw)
        monkeypatch.setattr(backend_mod.D365Backend, "__init__", counting_init)
        ctx.backend()
        ctx.invalidate_backend()
        ctx.backend()
        assert calls["n"] == 2

    def test_repl_root_callback_preserves_profile_and_password(self, monkeypatch, isolated_home):
        """Root cli() must NOT wipe profile_name/password when flags are omitted on a REPL line."""
        from click.testing import CliRunner
        from crm import cli as cli_mod
        ctx = cli_mod.CLIContext()
        ctx.profile_name = "myprofile"
        ctx.password = "pw"
        # Stub the eventual backend resolution path so we never hit real env.
        monkeypatch.setattr(cli_mod.CLIContext, "backend", lambda self: object())
        # Invoke `connection status` with no --profile/--password — mimics second
        # REPL line after `connection connect` already populated ctx.
        result = CliRunner().invoke(cli_mod.cli, ["connection", "status"], obj=ctx)
        assert result.exit_code == 0, result.output
        assert ctx.profile_name == "myprofile"
        assert ctx.password == "pw"


class TestSolutionExportFlags:
    def test_cli_export_setting_flag_maps_to_kwargs(self, monkeypatch, tmp_path):
        """CLI plumbing: --export-setting <name> → kwargs passed to export_solution."""
        from click.testing import CliRunner
        from crm import cli as cli_mod

        captured: dict[str, Any] = {}

        def fake_export_solution(_backend, unique_name, output, **kwargs):
            captured["unique_name"] = unique_name
            captured["output"] = output
            captured["kwargs"] = kwargs
            return {"output": str(output), "bytes": 0, "managed": False, "solution": unique_name}

        import crm.commands.solution as sol_commands_mod
        monkeypatch.setattr(sol_commands_mod.sol_mod, "export_solution", fake_export_solution)
        # Stub backend so no real connection resolution happens.
        monkeypatch.setattr(cli_mod.CLIContext, "backend", lambda self: object())

        out = tmp_path / "s.zip"
        result = CliRunner().invoke(
            cli_mod.cli,
            [
                "--json",
                "solution", "export", "MySol",
                "-o", str(out),
                "--export-setting", "customizations",
                "--export-setting", "general",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured["unique_name"] == "MySol"
        assert captured["kwargs"].get("export_customizations") is True
        assert captured["kwargs"].get("export_general") is True
        assert "export_calendar" not in captured["kwargs"]
        assert "export_sales" not in captured["kwargs"]


# ── solution.py — async flow ────────────────────────────────────────────


class TestExportSolutionAsync:
    OP_ID = "33333333-3333-3333-3333-333333333333"
    EXPORT_JOB_ID = "44444444-4444-4444-4444-444444444444"

    def test_export_calls_async_then_poll_then_download(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        out = tmp_path / "mysol.zip"
        # 5-byte zip stub, base64-encoded
        encoded = "UEsBAh4D"

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ExportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ExportJobId": self.EXPORT_JOB_ID,
            })
            m.get(backend.url_for(f"asyncoperations({self.OP_ID})"), json={
                "statecode": 3, "statuscode": 30, "message": "Done",
            })
            m.post(backend.url_for("DownloadSolutionExportData"), json={
                "ExportSolutionFile": encoded,
            })
            from crm.core import solution as sol_mod
            info = sol_mod.export_solution(
                backend, "MySolution", out, managed=True,
            )

        assert info["solution"] == "MySolution"
        assert info["managed"] is True
        assert info["output"] == str(out)
        assert info["async_operation_id"] == self.OP_ID
        assert info["export_job_id"] == self.EXPORT_JOB_ID
        assert info["bytes"] == 6  # base64 "UEsBAh4D" decodes to 6 bytes
        assert "duration_ms" in info
        assert out.exists()

    def test_export_raises_on_async_failure(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        out = tmp_path / "mysol.zip"

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ExportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ExportJobId": self.EXPORT_JOB_ID,
            })
            m.get(backend.url_for(f"asyncoperations({self.OP_ID})"), json={
                "statecode": 3, "statuscode": 31,
                "friendlymessage": "Solution export failed",
            })
            from crm.core import solution as sol_mod
            with pytest.raises(D365Error, match="export failed"):
                sol_mod.export_solution(backend, "MySolution", out)

        assert not out.exists()

    def test_export_dry_run_short_circuits(self, profile, tmp_path):
        dry = D365Backend(profile, password="pw", dry_run=True)
        from crm.core import solution as sol_mod
        info = sol_mod.export_solution(dry, "MySolution", tmp_path / "x.zip")
        assert info["_dry_run"] is True
        assert info["action"] == "ExportSolutionAsync"

    def test_export_settings_flags_serialize_into_request_body(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        encoded = "UEsBAh4D"

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ExportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ExportJobId": self.EXPORT_JOB_ID,
            })
            m.get(backend.url_for(f"asyncoperations({self.OP_ID})"), json={
                "statecode": 3, "statuscode": 30,
            })
            m.post(backend.url_for("DownloadSolutionExportData"), json={
                "ExportSolutionFile": encoded,
            })
            from crm.core import solution as sol_mod
            sol_mod.export_solution(
                backend, "MySol", tmp_path / "out.zip",
                managed=True,
                export_customizations=True,
                export_general=True,
            )

        async_post = next(
            r for r in m.request_history if r.url.endswith("ExportSolutionAsync")
        )
        body = json.loads(async_post.body)
        assert body["SolutionName"] == "MySol"
        assert body["Managed"] is True
        assert body["ExportCustomizationSettings"] is True
        assert body["ExportGeneralSettings"] is True
        assert body["ExportCalendarSettings"] is False
        assert body["ExportSales"] is False


class TestImportSolutionAsync:
    OP_ID = "55555555-5555-5555-5555-555555555555"

    def test_import_calls_async_then_polls(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ImportJobKey": "00000000-0000-0000-0000-000000000abc",
            })
            m.get(
                requests_mock.ANY,
                json={"statecode": 3, "statuscode": 30, "message": "Done"},
            )
            from crm.core import solution as sol_mod
            info = sol_mod.import_solution(backend, zip_path, quiet=True)

        assert info["async_operation_id"] == self.OP_ID
        assert info["status"] == "succeeded"
        assert info["action"] == "ImportSolutionAsync"
        assert info["import_job_id"] is not None
        assert "duration_ms" in info
        assert "started_on" in info or info.get("started_on") is None  # tolerant

    def test_import_missing_file_raises(self, backend, tmp_path):
        from crm.core import solution as sol_mod
        with pytest.raises(D365Error, match="not found"):
            sol_mod.import_solution(backend, tmp_path / "missing.zip")

    def test_import_raises_on_async_failure(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ImportJobKey": "00000000-0000-0000-0000-000000000abc",
            })
            m.get(
                requests_mock.ANY,
                json={"statecode": 3, "statuscode": 31,
                      "friendlymessage": "Import failed: missing dependency"},
            )
            from crm.core import solution as sol_mod
            with pytest.raises(D365Error, match="missing dependency"):
                sol_mod.import_solution(backend, zip_path, quiet=True)

    def test_import_dry_run_short_circuits(self, profile, tmp_path):
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        dry = D365Backend(profile, password="pw", dry_run=True)
        from crm.core import solution as sol_mod
        info = sol_mod.import_solution(dry, zip_path)
        assert info["_dry_run"] is True
        assert info["action"] == "ImportSolutionAsync"
        assert "import_job_id" in info

    _REJECT_BODY = {
        "error": {
            "message": "The parameter 'ImportJobId' in the request payload "
                       "is not a valid parameter for the operation 'ImportSolutionAsync'."
        }
    }
    _DATA_SUCCESS = (
        '<importexportxml><solutionManifests><solutionManifest>'
        '<UniqueName>Sol</UniqueName>'
        '<result result="success" errorcode="0" errortext="" />'
        '</solutionManifest></solutionManifests></importexportxml>'
    )

    def test_import_job_id_rejected_falls_back_to_sync_import(
        self, backend, tmp_path, monkeypatch
    ):
        """On-prem rejects ImportJobId on the async action → retry with the
        synchronous ImportSolution carrying the SAME client GUID, so the
        importjobs read-back works and import-result stays usable (#182)."""
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"),
                   status_code=400, json=self._REJECT_BODY)
            m.post(backend.url_for("ImportSolution"), status_code=204, text="")
            m.get(requests_mock.ANY, json={
                "progress": 100.0,
                "startedon": "2024-01-01T00:00:00Z",
                "completedon": "2024-01-01T00:01:00Z",
                "data": self._DATA_SUCCESS,
            })
            from crm.core import solution as sol_mod
            info = sol_mod.import_solution(backend, zip_path, quiet=True)

        async_req = next(
            r for r in m.request_history if r.url.endswith("ImportSolutionAsync")
        )
        sync_req = next(
            r for r in m.request_history if r.url.endswith("ImportSolution")
        )
        sent_id = json.loads(async_req.body)["ImportJobId"]
        assert json.loads(sync_req.body)["ImportJobId"] == sent_id
        assert info["import_job_id"] == sent_id
        assert info["async_operation_id"] is None
        assert info["status"] == "succeeded"
        assert info["action"] == "ImportSolution"
        assert info["result"] == "success"
        assert "warnings" not in info
        # read-back hit importjobs with the client GUID
        jobs_req = next(r for r in m.request_history if "importjobs(" in r.url)
        assert sent_id in jobs_req.url

    def test_sync_fallback_uses_long_read_timeout(
        self, backend, tmp_path, monkeypatch
    ):
        """The sync import runs in one HTTP request → its read timeout follows
        --timeout (else profile.async_timeout); other calls keep the default."""
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        def _run(timeout, expected):
            with requests_mock.Mocker() as m:
                m.post(backend.url_for("ImportSolutionAsync"),
                       status_code=400, json=self._REJECT_BODY)
                m.post(backend.url_for("ImportSolution"), status_code=204, text="")
                m.get(requests_mock.ANY, json={"progress": 100.0, "data": None})
                from crm.core import solution as sol_mod
                sol_mod.import_solution(backend, zip_path, quiet=True,
                                        timeout=timeout)
            async_req = next(r for r in m.request_history
                             if r.url.endswith("ImportSolutionAsync"))
            sync_req = next(r for r in m.request_history
                            if r.url.endswith("ImportSolution"))
            assert async_req.timeout == backend.profile.timeout
            assert sync_req.timeout == expected

        _run(timeout=1234, expected=1234)
        _run(timeout=None, expected=backend.profile.async_timeout)

    def test_sync_fallback_dependency_failure_raises_loudly(
        self, backend, tmp_path, monkeypatch
    ):
        """A missing-dependency import on the sync path faults synchronously —
        never a bare status:succeeded — and names the import job id (#182)."""
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"),
                   status_code=400, json=self._REJECT_BODY)
            m.post(backend.url_for("ImportSolution"), status_code=400, json={
                "error": {"code": "0x80048033",
                          "message": "The dependent component Entity (Id=foo) "
                                     "does not exist."}
            })
            from crm.core import solution as sol_mod
            with pytest.raises(D365Error, match="dependent component") as ex:
                sol_mod.import_solution(backend, zip_path, quiet=True)
        assert "import_job_id=" in str(ex.value)

    @pytest.mark.parametrize("job_row_resp", [
        {"json": {"progress": 100.0, "startedon": None,
                  "completedon": None, "data": None}},
        {"status_code": 404,
         "json": {"error": {"message": "importjob Does Not Exist"}}},
    ], ids=["empty-data", "no-row"])
    def test_sync_fallback_missing_results_warns_about_platform(
        self, backend, tmp_path, monkeypatch, job_row_resp
    ):
        """A successful sync import with no per-component results (empty data
        column or no importjob row) still succeeds, with an explicit warning
        explaining the platform gap (#182)."""
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"),
                   status_code=400, json=self._REJECT_BODY)
            m.post(backend.url_for("ImportSolution"), status_code=204, text="")
            m.get(requests_mock.ANY, **job_row_resp)
            from crm.core import solution as sol_mod
            info = sol_mod.import_solution(backend, zip_path, quiet=True)

        assert info["status"] == "succeeded"
        assert info["import_job_id"] is not None
        warnings = info.get("warnings") or []
        assert any("per-component" in w and "platform" in w for w in warnings)

    def test_sync_fallback_formatted_with_unreadable_row_still_succeeds(
        self, backend, tmp_path, monkeypatch
    ):
        """--formatted with an unreadable importjob row must not crash a
        succeeded sync import: the formatted fetch is skipped (#182)."""
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"),
                   status_code=400, json=self._REJECT_BODY)
            m.post(backend.url_for("ImportSolution"), status_code=204, text="")
            m.get(requests_mock.ANY, status_code=404,
                  json={"error": {"message": "importjob Does Not Exist"}})
            from crm.core import solution as sol_mod
            info = sol_mod.import_solution(backend, zip_path, quiet=True,
                                           formatted=True)

        assert info["status"] == "succeeded"
        assert "formatted_results" not in info
        warnings = info.get("warnings") or []
        assert any("per-component" in w for w in warnings)
        # no RetrieveFormattedImportJobResults round-trip was attempted
        assert not any("RetrieveFormatted" in r.url for r in m.request_history)

    def test_import_other_d365_error_reraised(
        self, backend, tmp_path, monkeypatch
    ):
        """A D365Error unrelated to ImportJobId is re-raised without retry."""
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        call_count: dict[str, int] = {"n": 0}

        def _post_handler(request, context):  # type: ignore[return]
            call_count["n"] += 1
            context.status_code = 403
            return {"error": {"message": "Caller does not have permission to ImportSolution."}}

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"), json=_post_handler)
            from crm.core import solution as sol_mod
            with pytest.raises(D365Error, match="permission"):
                sol_mod.import_solution(backend, zip_path, quiet=True)

        assert call_count["n"] == 1

class TestLoadPayload:
    def test_rejects_json_list_with_typename(self):
        import click
        from crm.commands._helpers import _load_payload

        with pytest.raises(click.UsageError, match=r"JSON object.*list"):
            _load_payload("[1,2,3]", None)

    @pytest.mark.parametrize("raw,typename", [
        ('"hello"', "str"),
        ("null", "NoneType"),
        ("42", "int"),
    ])
    def test_rejects_non_object_json(self, raw, typename):
        import click
        from crm.commands._helpers import _load_payload

        with pytest.raises(click.UsageError, match=rf"JSON object.*{typename}"):
            _load_payload(raw, None)

    def test_accepts_json_object(self):
        from crm.commands._helpers import _load_payload

        assert _load_payload('{"name":"x"}', None) == {"name": "x"}

    def test_data_at_prefix_hints_data_file(self):
        import click
        from crm.commands._helpers import _load_payload

        # `--data @file.json` is curl-style and parsed literally as JSON; point
        # the user at --data-file instead of the opaque "Expecting value" error.
        with pytest.raises(click.UsageError, match=r"--data-file"):
            _load_payload("@contact.json", None)

    def test_malformed_data_json_raises_usage_error(self):
        import click
        from crm.commands._helpers import _load_payload

        with pytest.raises(
            click.UsageError, match=r"invalid JSON in --data: .*Expecting"
        ):
            _load_payload('{"name": "x", bad}', None)

    def test_malformed_data_file_raises_usage_error(self, tmp_path):
        import click
        from crm.commands._helpers import _load_payload

        bad = tmp_path / "payload.json"
        bad.write_text('{"name": "x", bad}', encoding="utf-8")
        with pytest.raises(
            click.UsageError, match=r"invalid JSON in --data-file: .*Expecting"
        ):
            _load_payload(None, str(bad))

    def test_unreadable_data_file_raises_usage_error(self, tmp_path):
        import click
        from crm.commands._helpers import _load_payload

        # a directory passes Click's exists=True at the CLI layer in callers
        # without dir_okay=False, and open() on it raises OSError
        with pytest.raises(click.UsageError, match=r"cannot read --data-file:"):
            _load_payload(None, str(tmp_path))


class TestErrorEnvelope:
    def test_error_envelope_null_when_status_missing(self, capsys):
        import click

        from crm.cli import CLIContext
        from crm.utils.d365_backend import D365Error
        ctx = CLIContext()
        ctx.json_mode = True
        exc = D365Error("transport boom")  # no status, no code
        # Mirror cli._handle_d365_error after the fix: emit prints the envelope,
        # then raises Exit(1) per ADR 0001 (operational failure → exit 1).
        with pytest.raises(click.exceptions.Exit):
            ctx.emit(False, error=str(exc), meta={"status": exc.status, "code": exc.code})
        out = capsys.readouterr().out
        envelope = json.loads(out)
        assert envelope["ok"] is False
        assert envelope["error"] == "transport boom"
        assert envelope["meta"]["status"] is None
        assert envelope["meta"]["code"] is None
