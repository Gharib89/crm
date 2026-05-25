"""Unit tests for crm.core.optionsets."""
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


_OS_ID = "44444444-4444-4444-4444-444444444444"


class TestListOptionsets:
    def test_list_all(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": "new_priority", "IsCustomOptionSet": True, "IsGlobal": True},
                    {"Name": "statecode", "IsCustomOptionSet": False, "IsGlobal": True},
                ]},
            )
            rows = os_mod.list_optionsets(backend)
        assert len(rows) == 2

    def test_list_custom_only_filters(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": "new_priority", "IsCustomOptionSet": True},
                    {"Name": "statecode", "IsCustomOptionSet": False},
                ]},
            )
            rows = os_mod.list_optionsets(backend, custom_only=True)
        assert len(rows) == 1
        assert rows[0]["Name"] == "new_priority"

    def test_list_top_slice(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions"),
                json={"value": [
                    {"Name": f"opt_{i}"} for i in range(5)
                ]},
            )
            rows = os_mod.list_optionsets(backend, top=2)
        assert len(rows) == 2


class TestGetOptionset:
    def test_get_expands_options(self, backend):
        from crm.core import optionsets as os_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json={"Name": "new_priority", "Options": [
                    {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}}
                ]},
            )
            info = os_mod.get_optionset(backend, "new_priority")
        assert info["Name"] == "new_priority"
        assert info["Options"][0]["Value"] == 1


class TestCreateOptionset:
    def test_create_with_options(self, backend):
        from crm.core import optionsets as os_mod
        url = backend.url_for(f"GlobalOptionSetDefinitions({_OS_ID})")
        with requests_mock.Mocker() as m:
            m.post(
                backend.url_for("GlobalOptionSetDefinitions"),
                status_code=204,
                headers={"OData-EntityId": url},
            )
            m.get(
                url,
                json={"Name": "new_priority", "IsCustomOptionSet": True},
            )
            info = os_mod.create_optionset(
                backend,
                name="new_priority",
                display_name="Priority",
                options=[(1, "Low"), (2, "Medium"), (3, "High")],
                solution="DevSolution",
            )
        assert info["created"] is True
        assert info["name"] == "new_priority"
        body = m.request_history[0].json()
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.OptionSetMetadata"
        assert body["Name"] == "new_priority"
        assert body["IsGlobal"] is True
        assert body["Options"][0]["Value"] == 1
        assert m.request_history[0].headers["MSCRM.SolutionUniqueName"] == "DevSolution"

    def test_create_rejects_duplicate_values(self, backend):
        from crm.core import optionsets as os_mod
        with pytest.raises(D365Error, match="Duplicate"):
            os_mod.create_optionset(
                backend, name="new_dupe", display_name="Dupe",
                options=[(1, "A"), (1, "B")],
            )
