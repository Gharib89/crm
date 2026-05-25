"""Unit tests for metadata.delete_entity."""
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


class TestDeleteEntity:
    def test_refuses_non_custom_entity(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')"),
                json={"LogicalName": "account", "IsCustomEntity": False, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="not a custom entity"):
                meta_mod.delete_entity(backend, "account")

    def test_refuses_managed_entity(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='managed_thing')"),
                json={"LogicalName": "managed_thing", "IsCustomEntity": True, "IsManaged": True},
            )
            with pytest.raises(D365Error, match="managed"):
                meta_mod.delete_entity(backend, "managed_thing")

    def test_happy_path_deletes_with_solution_header(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"LogicalName": "new_widget", "IsCustomEntity": True, "IsManaged": False},
            )
            m.delete(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                status_code=204,
            )
            info = meta_mod.delete_entity(backend, "new_widget", solution="DevSolution")
        assert info["deleted"] is True
        assert info["logical_name"] == "new_widget"
        assert info["solution"] == "DevSolution"
        delete_req = m.request_history[-1]
        assert delete_req.method == "DELETE"
        assert delete_req.headers.get("MSCRM.SolutionUniqueName") == "DevSolution"

    def test_delete_server_failure_surfaces_d365error(self, backend):
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"LogicalName": "new_widget", "IsCustomEntity": True, "IsManaged": False},
            )
            m.delete(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                status_code=400,
                json={"error": {"code": "0x80048404", "message": "Cannot delete: dependencies exist"}},
            )
            with pytest.raises(D365Error, match="dependencies"):
                meta_mod.delete_entity(backend, "new_widget")
