"""Unit tests for metadata list-actions / list-functions."""
# pyright: basic
from __future__ import annotations

import requests_mock

from crm.core.metadata import list_actions, list_functions


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


class TestAcceptHeader:
    """$metadata is served as XML (CSDL); requesting it with the default
    JSON Accept header returns HTTP 415 (issue #266)."""

    def test_metadata_fetch_requests_xml(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            list_actions(backend)
        assert m.last_request.headers["Accept"] == "application/xml"

    def test_functions_fetch_requests_xml(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            list_functions(backend)
        assert m.last_request.headers["Accept"] == "application/xml"

    def test_other_get_keeps_json_accept(self, backend):
        """Regression: only the $metadata path flips to XML; ordinary
        Web API GETs still advertise JSON."""
        with requests_mock.Mocker() as m:
            m.get(
                "https://crm.contoso.local/contoso/api/data/v9.2/contacts",
                json={"value": []},
            )
            backend.get("contacts")
        assert m.last_request.headers["Accept"] == "application/json"
