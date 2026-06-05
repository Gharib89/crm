# pyright: basic
"""Tests for crm/core/dependencies.py — resolve + RetrieveDependenciesForDelete.

All HTTP mocked via requests_mock; no live D365 server.
"""
from __future__ import annotations

import pytest
import requests_mock as req_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error
from crm.core import dependencies as dep_mod


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
def backend(profile: ConnectionProfile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


@pytest.fixture
def dry_backend(profile: ConnectionProfile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=True)


# ── resolve_target ────────────────────────────────────────────────────────


class TestResolveTarget:
    METADATA_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    def test_resolve_entity(self, backend: D365Backend) -> None:
        url = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with req_mock.Mocker() as m:
            m.get(url, json={"MetadataId": self.METADATA_ID})
            mid, ct = dep_mod.resolve_target(backend, "entity", "new_widget")
        assert mid == self.METADATA_ID
        assert ct == 1
        assert m.last_request.qs.get("$select") == ["metadataid"]

    def test_resolve_attribute(self, backend: D365Backend) -> None:
        url = backend.url_for(
            "EntityDefinitions(LogicalName='new_widget')/Attributes(LogicalName='new_amount')"
        )
        with req_mock.Mocker() as m:
            m.get(url, json={"MetadataId": self.METADATA_ID})
            mid, ct = dep_mod.resolve_target(backend, "attribute", "new_widget.new_amount")
        assert mid == self.METADATA_ID
        assert ct == 2

    def test_resolve_optionset(self, backend: D365Backend) -> None:
        url = backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')")
        with req_mock.Mocker() as m:
            m.get(url, json={"MetadataId": self.METADATA_ID})
            mid, ct = dep_mod.resolve_target(backend, "optionset", "new_priority")
        assert mid == self.METADATA_ID
        assert ct == 9

    def test_resolve_relationship(self, backend: D365Backend) -> None:
        url = backend.url_for("RelationshipDefinitions(SchemaName='new_rel')")
        with req_mock.Mocker() as m:
            m.get(url, json={"MetadataId": self.METADATA_ID})
            mid, ct = dep_mod.resolve_target(backend, "relationship", "new_rel")
        assert mid == self.METADATA_ID
        assert ct == 10

    def test_resolve_404_raises_not_found(self, backend: D365Backend) -> None:
        url = backend.url_for("EntityDefinitions(LogicalName='no_such')")
        with req_mock.Mocker() as m:
            m.get(url, status_code=404, json={"error": {"code": "0x", "message": "not found"}})
            with pytest.raises(D365Error, match="not found"):
                dep_mod.resolve_target(backend, "entity", "no_such")

    def test_unknown_kind_raises(self, backend: D365Backend) -> None:
        with pytest.raises(D365Error, match="unknown kind"):
            dep_mod.resolve_target(backend, "bogus", "something")

    def test_empty_target_raises(self, backend: D365Backend) -> None:
        with pytest.raises(D365Error):
            dep_mod.resolve_target(backend, "entity", "")

    def test_attribute_no_dot_raises(self, backend: D365Backend) -> None:
        with pytest.raises(D365Error, match="dotted"):
            dep_mod.resolve_target(backend, "attribute", "new_widget")

    def test_non_404_reraised(self, backend: D365Backend) -> None:
        url = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with req_mock.Mocker() as m:
            m.get(url, status_code=500, json={"error": {"code": "0x", "message": "boom"}})
            with pytest.raises(D365Error) as exc_info:
                dep_mod.resolve_target(backend, "entity", "new_widget")
        assert exc_info.value.status == 500


# ── build_dependency_path ─────────────────────────────────────────────────


class TestBuildDependencyPath:
    def test_delete_path(self) -> None:
        path = dep_mod.build_dependency_path("abc-123", 9, for_="delete")
        assert "RetrieveDependenciesForDelete" in path
        assert "abc-123" in path
        assert "9" in path

    def test_dependents_path(self) -> None:
        path = dep_mod.build_dependency_path("abc-123", 1, for_="dependents")
        assert "RetrieveDependentComponents" in path

    def test_unknown_for_raises(self) -> None:
        with pytest.raises(D365Error, match="unknown for_"):
            dep_mod.build_dependency_path("abc-123", 9, for_="bogus")

    def test_inline_literal_encoding(self) -> None:
        """GUID and int must be UNQUOTED inline — no quotes around either."""
        guid = "00000000-0000-0000-0000-000000000001"
        path = dep_mod.build_dependency_path(guid, 9, for_="delete")
        # e.g. RetrieveDependenciesForDelete(ObjectId=00000000-...,ComponentType=9)
        assert f"ObjectId={guid}" in path
        assert "ComponentType=9" in path
        # Must NOT have quotes around the guid
        assert f"ObjectId='{guid}'" not in path
        assert f'ObjectId="{guid}"' not in path


# ── retrieve_dependencies ─────────────────────────────────────────────────


ENTITY_ID = "00000000-0000-0000-0000-000000000001"
DEP_ATTR_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DEP_PARENT_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _mock_entity_resolve(m: req_mock.Mocker, backend: D365Backend) -> None:
    url = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
    m.get(url, json={"MetadataId": ENTITY_ID})


def _mock_dep_function(
    m: req_mock.Mocker,
    backend: D365Backend,
    *,
    func: str = "RetrieveDependenciesForDelete",
    component_type: int = 1,
    records: list[dict] | None = None,
) -> str:
    path = f"{func}(ObjectId={ENTITY_ID},ComponentType={component_type})"
    url = backend.url_for(path)
    m.get(url, json={"value": records or []})
    return url


class TestRetrieveDependencies:
    def test_delete_issues_correct_url(self, backend: D365Backend) -> None:
        with req_mock.Mocker() as m:
            _mock_entity_resolve(m, backend)
            fn_url = _mock_dep_function(m, backend, func="RetrieveDependenciesForDelete")
            result = dep_mod.retrieve_dependencies(backend, "entity", "new_widget", for_="delete")
        assert result["for"] == "delete"
        assert result["kind"] == "entity"
        # Verify the actual function URL was requested
        fn_path = f"RetrieveDependenciesForDelete(ObjectId={ENTITY_ID},ComponentType=1)"
        assert any(fn_path in r.url for r in m.request_history)

    def test_dependents_issues_correct_url(self, backend: D365Backend) -> None:
        with req_mock.Mocker() as m:
            _mock_entity_resolve(m, backend)
            _mock_dep_function(m, backend, func="RetrieveDependentComponents")
            dep_mod.retrieve_dependencies(backend, "entity", "new_widget", for_="dependents")
        fn_path = f"RetrieveDependentComponents(ObjectId={ENTITY_ID},ComponentType=1)"
        assert any(fn_path in r.url for r in m.request_history)

    def test_can_delete_true_when_empty(self, backend: D365Backend) -> None:
        with req_mock.Mocker() as m:
            _mock_entity_resolve(m, backend)
            _mock_dep_function(m, backend, records=[])
            result = dep_mod.retrieve_dependencies(backend, "entity", "new_widget")
        assert result["can_delete"] is True
        assert result["blockers"] == []

    def test_can_delete_false_with_blocker(self, backend: D365Backend) -> None:
        blocker_record = {
            "dependentcomponenttype": 2,
            "dependentcomponentobjectid": DEP_ATTR_ID,
            "dependentcomponentparentid": DEP_PARENT_ID,
            "requiredcomponenttype": 9,
            "requiredcomponentobjectid": ENTITY_ID,
            "dependencytype": 1,
        }
        with req_mock.Mocker() as m:
            _mock_entity_resolve(m, backend)
            _mock_dep_function(m, backend, records=[blocker_record])
            result = dep_mod.retrieve_dependencies(backend, "entity", "new_widget")
        assert result["can_delete"] is False
        assert len(result["blockers"]) == 1
        b = result["blockers"][0]
        assert b["dependent_type"] == "Attribute"
        assert b["required_type"] == "Option Set"
        assert b["dependent_id"] == DEP_ATTR_ID
        assert b["dependent_parent_id"] == DEP_PARENT_ID

    def test_metadata_id_and_component_type_in_result(self, backend: D365Backend) -> None:
        with req_mock.Mocker() as m:
            _mock_entity_resolve(m, backend)
            _mock_dep_function(m, backend)
            result = dep_mod.retrieve_dependencies(backend, "entity", "new_widget")
        assert result["metadata_id"] == ENTITY_ID
        assert result["component_type"] == 1

    def test_dry_run_still_issues_get(self, dry_backend: D365Backend) -> None:
        """dry-run-off trick: read-only GETs must fire even under dry_run=True."""
        with req_mock.Mocker() as m:
            url = dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')")
            m.get(url, json={"MetadataId": ENTITY_ID})
            fn_path = (
                f"RetrieveDependenciesForDelete(ObjectId={ENTITY_ID},ComponentType=1)"
            )
            m.get(dry_backend.url_for(fn_path), json={"value": []})
            result = dep_mod.retrieve_dependencies(dry_backend, "entity", "new_widget")
        assert len(m.request_history) >= 2
        assert result["can_delete"] is True
        # backend should still be in dry_run mode after the call
        assert dry_backend.dry_run is True


# ── dependencies_by_id ────────────────────────────────────────────────────


class TestDependenciesById:
    def test_skips_resolve(self, backend: D365Backend) -> None:
        """dependencies_by_id accepts pre-resolved id and skips the resolve GET."""
        with req_mock.Mocker() as m:
            fn_path = (
                f"RetrieveDependenciesForDelete(ObjectId={ENTITY_ID},ComponentType=1)"
            )
            m.get(backend.url_for(fn_path), json={"value": []})
            result = dep_mod.dependencies_by_id(backend, ENTITY_ID, 1, for_="delete")
        # Only one request (the function call), no resolve GET
        assert len(m.request_history) == 1
        assert result["can_delete"] is True
        assert result["metadata_id"] == ENTITY_ID
        assert result["component_type"] == 1


# ── _component_label ──────────────────────────────────────────────────────


class TestComponentLabel:
    def test_known_codes(self) -> None:
        assert dep_mod._component_label(1) == "Entity"
        assert dep_mod._component_label(2) == "Attribute"
        assert dep_mod._component_label(9) == "Option Set"
        assert dep_mod._component_label(10) == "Entity Relationship"

    def test_unknown_code_falls_back_to_str(self) -> None:
        assert dep_mod._component_label(9999) == "9999"
