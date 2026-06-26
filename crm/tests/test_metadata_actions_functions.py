"""Unit tests for metadata list-actions / list-functions."""
# pyright: basic
from __future__ import annotations

import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
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
        <ReturnType Type="mscrm.ImportSolutionResponse" />
      </Action>
      <Action Name="AddMembersTeam" IsBound="true">
        <Parameter Name="entity" Type="mscrm.team" />
      </Action>
      <Function Name="RetrieveTotalRecordCount">
        <Parameter Name="EntityNames" Type="Collection(Edm.String)" />
        <ReturnType Type="mscrm.RetrieveTotalRecordCountResponse" />
      </Function>
      <Function Name="WhoAmI" />
      <Function Name="RetrieveAllChildUsersSystemUser"
                IsBound="true" IsComposable="true">
        <Parameter Name="entity" Type="mscrm.systemuser" />
        <ReturnType Type="Collection(mscrm.systemuser)" />
      </Function>
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
        assert names == ["PublishAllXml", "ImportSolution", "AddMembersTeam"]

    def test_is_bound_defaults_false_and_reflects_attribute(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            actions = list_actions(backend)
        by_name = {a["name"]: a for a in actions}
        assert by_name["PublishAllXml"]["is_bound"] is False
        assert by_name["AddMembersTeam"]["is_bound"] is True

    def test_return_type_from_child_element_or_null(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            actions = list_actions(backend)
        by_name = {a["name"]: a for a in actions}
        assert by_name["ImportSolution"]["return_type"] == "mscrm.ImportSolutionResponse"
        assert by_name["PublishAllXml"]["return_type"] is None

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
        assert names == [
            "RetrieveTotalRecordCount", "WhoAmI", "RetrieveAllChildUsersSystemUser",
        ]

    def test_is_composable_defaults_false_and_reflects_attribute(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            functions = list_functions(backend)
        by_name = {f["name"]: f for f in functions}
        assert by_name["WhoAmI"]["is_composable"] is False
        assert by_name["RetrieveAllChildUsersSystemUser"]["is_composable"] is True

    def test_is_bound_and_return_type_on_functions(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            functions = list_functions(backend)
        by_name = {f["name"]: f for f in functions}
        assert by_name["WhoAmI"]["is_bound"] is False
        assert by_name["WhoAmI"]["return_type"] is None
        rtrc = by_name["RetrieveTotalRecordCount"]
        assert rtrc["is_bound"] is False
        assert rtrc["return_type"] == "mscrm.RetrieveTotalRecordCountResponse"

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


_CANNED_ACTIONS = [
    {"name": "PublishAllXml", "is_bound": False, "return_type": None,
     "parameters": []},
    {"name": "AddMembersTeam", "is_bound": True,
     "return_type": "mscrm.AddMembersTeamResponse",
     "parameters": [{"name": "entity", "type": "mscrm.team"}]},
]

_CANNED_FUNCTIONS = [
    {"name": "WhoAmI", "is_bound": False, "is_composable": False,
     "return_type": "mscrm.WhoAmIResponse", "parameters": []},
    {"name": "RetrieveAllChildUsersSystemUser", "is_bound": True,
     "is_composable": True, "return_type": "Collection(mscrm.systemuser)",
     "parameters": [{"name": "entity", "type": "mscrm.systemuser"}]},
]


class TestListActionsHumanTable:
    def test_columns_show_bound_and_return_type(self, monkeypatch):
        monkeypatch.setattr(
            "crm.core.metadata.list_actions", lambda backend: _CANNED_ACTIONS)
        monkeypatch.setattr(CLIContext, "backend", lambda self: object())
        result = CliRunner().invoke(cli, ["metadata", "list-actions"])
        assert result.exit_code == 0, result.output
        assert "Bound" in result.output
        assert "Returns" in result.output
        assert "mscrm.AddMembersTeamResponse" in result.output


class TestListFunctionsHumanTable:
    def test_columns_show_bound_composable_and_return_type(self, monkeypatch):
        monkeypatch.setattr(
            "crm.core.metadata.list_functions", lambda backend: _CANNED_FUNCTIONS)
        monkeypatch.setattr(CLIContext, "backend", lambda self: object())
        result = CliRunner().invoke(cli, ["metadata", "list-functions"])
        assert result.exit_code == 0, result.output
        assert "Bound" in result.output
        assert "Composable" in result.output
        assert "Returns" in result.output
        assert "Collection(mscrm.systemuser)" in result.output
