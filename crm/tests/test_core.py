"""Unit tests for crm core modules.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import json
from pathlib import Path
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


# ── Fixtures ────────────────────────────────────────────────────────────


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


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".d365"))
    return tmp_path / ".d365"


# ── connection.py ───────────────────────────────────────────────────────


class TestConnectionEnv:
    def test_profile_from_env_happy_path(self, monkeypatch, tmp_path):
        # Isolate from any developer .env / CRM_* aliases in the surrounding shell.
        monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
        for k in (conn_mod.ENV_API_VERSION, "CRM_API_VERSION"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv(conn_mod.ENV_URL, "https://crm.x.local/org")
        monkeypatch.setenv(conn_mod.ENV_DOMAIN, "CONTOSO")
        monkeypatch.setenv(conn_mod.ENV_USERNAME, "alice")
        monkeypatch.setenv(conn_mod.ENV_AUTH, "ntlm")
        p = conn_mod.profile_from_env()
        assert p.url == "https://crm.x.local/org"
        assert p.domain == "CONTOSO"
        assert p.username == "alice"
        assert p.api_version == "v9.2"

    def test_profile_from_env_missing_url(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
        for k in (conn_mod.ENV_URL, "CRM_BASE_URL", "CRM_URL"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(D365Error, match="D365_URL"):
            conn_mod.profile_from_env()

    def test_profile_from_env_rejects_non_ntlm_auth(self, monkeypatch):
        monkeypatch.setenv(conn_mod.ENV_URL, "https://crm.x.local/org")
        monkeypatch.setenv(conn_mod.ENV_USERNAME, "alice")
        monkeypatch.setenv(conn_mod.ENV_AUTH, "oauth")
        with pytest.raises(D365Error, match="ntlm"):
            conn_mod.profile_from_env()

    def test_resolve_credentials_requires_password(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
        monkeypatch.setenv(conn_mod.ENV_URL, "https://crm.x.local/org")
        monkeypatch.setenv(conn_mod.ENV_USERNAME, "alice")
        for k in (conn_mod.ENV_PASSWORD, "CRM_PASSWORD", "CRM_PASS"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(D365Error, match="No password"):
            conn_mod.resolve_credentials()


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

    def test_delete_returns_id_payload(self, backend):
        with requests_mock.Mocker() as m:
            m.delete(backend.url_for(f"contacts({_GUID})"), status_code=204)
            result = entity_mod.delete(backend, "contacts", _GUID)
        assert result["deleted"] is True
        assert result["id"] == _GUID

    def test_invalid_guid_rejected(self, backend):
        with pytest.raises(D365Error, match="Invalid record id"):
            entity_mod.retrieve(backend, "contacts", "not-a-guid")


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
        target = Path(isolated_home) / "sessions" / "x.json"
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
        with requests_mock.Mocker() as m:
            m.get(
                requests_mock.ANY,
                json={
                    "LogicalName": "industrycode",
                    "OptionSet": {"Options": [
                        {"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Accounting"}}},
                        {"Value": 2, "Label": {"UserLocalizedLabel": {"Label": "Agri"}}},
                    ]},
                },
            )
            info = meta_mod_local.picklist_options(backend, "account", "industrycode")
        assert info["LogicalName"] == "industrycode"
        assert info["OptionSet"]["Options"][0]["Value"] == 1
        url = m.request_history[0].url
        assert "PicklistAttributeMetadata" in url
        assert "%24expand=OptionSet" in url or "$expand=OptionSet" in url


class TestCreateEntity:
    def test_create_entity_posts_expected_payload(self, backend):
        from crm.core import metadata as meta_mod_local
        with requests_mock.Mocker() as m:
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

        req = m.request_history[0]
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


class TestConnectionDotenv:
    def test_load_dotenv_reads_crm_aliases_and_does_not_override(self, tmp_path, monkeypatch):
        for k in ("D365_URL", "CRM_BASE_URL", "D365_USERNAME", "CRM_USERNAME",
                  "D365_PASSWORD", "CRM_PASSWORD", "D365_DOMAIN", "CRM_DOMAIN"):
            monkeypatch.delenv(k, raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "CRM_BASE_URL=http://crm.x.local/MOCE\n"
            "CRM_API_VERSION=v9.1\n"
            "CRM_USERNAME=moce\\admin\n"
            "CRM_PASSWORD=pw\n"
            "CRM_AUTH=ntlm\n"
        )
        from crm.core import connection as conn_mod_local
        loaded = conn_mod_local.load_dotenv(env_file)
        assert loaded == env_file
        profile = conn_mod_local.profile_from_env()
        assert profile.url == "http://crm.x.local/MOCE"
        assert profile.api_version == "v9.1"
        assert profile.domain == "moce"
        assert profile.username == "admin"

    def test_dotenv_preserves_inner_quotes(self, tmp_path, monkeypatch):
        for k in ("KEY_WITH_QUOTE", "D365_URL", "CRM_BASE_URL"):
            monkeypatch.delenv(k, raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text('KEY_WITH_QUOTE="foo\'s bar"\n')
        from crm.core import connection as conn_mod_local
        conn_mod_local.load_dotenv(env_file)
        import os
        assert os.environ["KEY_WITH_QUOTE"] == "foo's bar"


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
            "crm.cli.conn_mod.resolve_credentials",
            lambda profile_name=None, password_override=None:
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
            "crm.cli.conn_mod.resolve_credentials",
            lambda profile_name=None, password_override=None:
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
    def test_export_solution_passes_flags_to_body(self, backend, tmp_path):
        from crm.core import solution as sol_mod_local
        import base64
        with requests_mock.Mocker() as m:
            payload = base64.b64encode(b"FAKE-ZIP-CONTENT").decode("ascii")
            m.post(
                backend.url_for("ExportSolution"),
                json={"ExportSolutionFile": payload},
            )
            out = tmp_path / "s.zip"
            sol_mod_local.export_solution(
                backend, "MySol", out,
                export_customizations=True,
                export_general=True,
            )
        body = json.loads(m.request_history[0].body)
        assert body["SolutionName"] == "MySol"
        assert body["ExportCustomizationSettings"] is True
        assert body["ExportGeneralSettings"] is True
        # Other flags default to False
        assert body["ExportCalendarSettings"] is False
        assert body["ExportSales"] is False
        assert body["ExportAutoNumberingSettings"] is False

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

        monkeypatch.setattr(cli_mod.sol_mod, "export_solution", fake_export_solution)
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


class TestErrorEnvelope:
    def test_error_envelope_null_when_status_missing(self, capsys):
        from crm.cli import CLIContext
        from crm.utils.d365_backend import D365Error
        ctx = CLIContext()
        ctx.json_mode = True
        exc = D365Error("transport boom")  # no status, no code
        # Mirror cli._handle_d365_error after the fix:
        ctx.emit(False, error=str(exc), meta={"status": exc.status, "code": exc.code})
        out = capsys.readouterr().out
        envelope = json.loads(out)
        assert envelope["ok"] is False
        assert envelope["error"] == "transport boom"
        assert envelope["meta"]["status"] is None
        assert envelope["meta"]["code"] is None
