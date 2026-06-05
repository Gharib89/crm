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


_ENTITY_META_ID = "aaaa0001-0000-0000-0000-000000000000"


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

    def test_check_dependencies_off_by_default_no_extra_get(self, backend):
        """Without --check-dependencies, no dependency GETs fire."""
        from crm.core import metadata as meta_mod
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={"IsCustomEntity": True, "IsManaged": False},
            )
            m.delete(backend.url_for("EntityDefinitions(LogicalName='new_widget')"), status_code=204)
            info = meta_mod.delete_entity(backend, "new_widget")
        assert "can_delete" not in info
        assert "blockers" not in info
        # Only pre-flight GET + DELETE — no RetrieveDependenciesForDelete
        dep_reqs = [r for r in m.request_history if "RetrieveDependencies" in r.url]
        assert dep_reqs == []

    def test_check_dependencies_with_blockers(self, backend):
        """check_dependencies=True fires resolve GET + function GET; blockers appear in result."""
        from crm.core import metadata as meta_mod
        dep_url = backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_ENTITY_META_ID},ComponentType=1)"
        )
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={
                    "IsCustomEntity": True, "IsManaged": False,
                    "MetadataId": _ENTITY_META_ID,
                },
            )
            m.get(dep_url, json={"value": [
                {
                    "dependentcomponenttype": 24,
                    "dependentcomponentobjectid": "bbbb0001-0000-0000-0000-000000000000",
                    "dependentcomponentparentid": None,
                    "requiredcomponenttype": 1,
                    "dependencytype": 2,
                },
            ]})
            m.delete(backend.url_for("EntityDefinitions(LogicalName='new_widget')"), status_code=204)
            info = meta_mod.delete_entity(backend, "new_widget", check_dependencies=True)
        assert info["deleted"] is True
        assert info["can_delete"] is False
        assert len(info["blockers"]) == 1
        assert info["blockers"][0]["dependent_type"] == "Form"
        # The function GET must have fired
        dep_reqs = [r for r in m.request_history if "RetrieveDependencies" in r.url]
        assert len(dep_reqs) == 1

    def test_check_dependencies_no_blockers_can_delete_true(self, backend):
        """When RetrieveDependenciesForDelete returns empty value, can_delete is True."""
        from crm.core import metadata as meta_mod
        dep_url = backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_ENTITY_META_ID},ComponentType=1)"
        )
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={
                    "IsCustomEntity": True, "IsManaged": False,
                    "MetadataId": _ENTITY_META_ID,
                },
            )
            m.get(dep_url, json={"value": []})
            m.delete(backend.url_for("EntityDefinitions(LogicalName='new_widget')"), status_code=204)
            info = meta_mod.delete_entity(backend, "new_widget", check_dependencies=True)
        assert info["can_delete"] is True
        assert info["blockers"] == []


class TestDeleteEntityDryRun:
    """Dry-run delete_entity returns _dry_run preview, not {deleted: True}."""

    def test_dryrun_returns_preview_not_deleted(self, profile):
        from crm.core import metadata as meta_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={
                    "LogicalName": "new_widget",
                    "IsCustomEntity": True,
                    "IsManaged": False,
                    "MetadataId": _ENTITY_META_ID,
                },
            )
            info = meta_mod.delete_entity(dry_backend, "new_widget")
        assert info.get("_dry_run") is True
        assert info.get("would_delete") is True
        assert "deleted" not in info
        assert info["logical_name"] == "new_widget"
        # No DELETE should have hit the server
        delete_reqs = [r for r in m.request_history if r.method == "DELETE"]
        assert delete_reqs == []

    def test_dryrun_with_check_dependencies_merges_blockers(self, profile):
        from crm.core import metadata as meta_mod
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        dep_url = dry_backend.url_for(
            f"RetrieveDependenciesForDelete(ObjectId={_ENTITY_META_ID},ComponentType=1)"
        )
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')"),
                json={
                    "IsCustomEntity": True, "IsManaged": False,
                    "MetadataId": _ENTITY_META_ID,
                },
            )
            m.get(dep_url, json={"value": []})
            info = meta_mod.delete_entity(dry_backend, "new_widget", check_dependencies=True)
        assert info.get("_dry_run") is True
        assert info.get("would_delete") is True
        assert "deleted" not in info
        assert info["can_delete"] is True
        assert info["blockers"] == []
        delete_reqs = [r for r in m.request_history if r.method == "DELETE"]
        assert delete_reqs == []


class TestDeleteEntityCommand:
    """Command-layer smoke test: --check-dependencies threads through to the core fn."""

    def test_check_dependencies_flag_plumbs_through(self, profile, monkeypatch):
        from click.testing import CliRunner
        from crm.commands.metadata import metadata_group
        from crm.core import metadata as meta_mod
        captured = {}

        original = meta_mod.delete_entity

        def fake_delete_entity(backend, logical_name, *, solution=None, check_dependencies=False):
            captured["check_dependencies"] = check_dependencies
            return {"deleted": True, "logical_name": logical_name, "solution": solution}

        monkeypatch.setattr(meta_mod, "delete_entity", fake_delete_entity)

        runner = CliRunner()
        result = runner.invoke(
            metadata_group,
            ["delete-entity", "new_widget", "--yes", "--check-dependencies"],
            catch_exceptions=False,
            env={
                "D365_URL": "https://crm.contoso.local/contoso",
                "D365_USERNAME": "alice",
                "D365_PASSWORD": "pw",
                "D365_DOMAIN": "CONTOSO",
            },
        )
        assert result.exit_code == 0
        assert captured.get("check_dependencies") is True
