"""Unit tests for metadata list-actions / list-functions."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.core.metadata import list_actions, list_functions
from crm.utils.d365_backend import ConnectionProfile, D365Backend


_SAMPLE_METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm"
            Namespace="Microsoft.Dynamics.CRM">
      <Action Name="PublishAllXml" />
      <Action Name="ImportSolution">
        <Parameter Name="CustomizationFile" Type="Edm.Binary" />
        <Parameter Name="PublishWorkflows" Type="Edm.Boolean" />
      </Action>
      <Function Name="RetrieveTotalRecordCount">
        <Parameter Name="EntityNames" Type="Collection(Edm.String)" />
      </Function>
      <Function Name="WhoAmI" />
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


@pytest.fixture
def backend():
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", verify_ssl=False,
    )
    return D365Backend(profile, password="pw")


def _mock_metadata(m: requests_mock.Mocker) -> None:
    m.get(
        "https://crm.contoso.local/contoso/api/data/v9.2/$metadata",
        text=_SAMPLE_METADATA_XML,
        headers={"Content-Type": "application/xml"},
    )


class TestListActions:
    def test_returns_action_names(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            actions = list_actions(backend)
        names = [a["name"] for a in actions]
        assert names == ["PublishAllXml", "ImportSolution"]

    def test_parameters_included(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            actions = list_actions(backend)
        import_solution = next(a for a in actions if a["name"] == "ImportSolution")
        assert import_solution["parameters"] == [
            {"name": "CustomizationFile", "type": "Edm.Binary"},
            {"name": "PublishWorkflows", "type": "Edm.Boolean"},
        ]


class TestListFunctions:
    def test_returns_function_names(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            functions = list_functions(backend)
        names = [f["name"] for f in functions]
        assert names == ["RetrieveTotalRecordCount", "WhoAmI"]

    def test_collection_parameter_type(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            functions = list_functions(backend)
        rtrc = next(f for f in functions if f["name"] == "RetrieveTotalRecordCount")
        assert rtrc["parameters"] == [
            {"name": "EntityNames", "type": "Collection(Edm.String)"},
        ]
