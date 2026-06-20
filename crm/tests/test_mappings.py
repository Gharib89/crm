"""Unit tests for crm.core.mappings (field/attribute mappings + AutoMapEntity)."""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.utils.d365_backend import D365Backend, D365Error
from crm.core import mappings as mp


_REL = "new_account_new_widget"
_REL_PATH = (
    "RelationshipDefinitions(SchemaName='new_account_new_widget')"
    "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
)
_PAIR = {"ReferencedEntity": "account", "ReferencingEntity": "new_widget"}
_MAP_ID = "33333333-3333-3333-3333-333333333333"


class TestCreateMapping:
    def test_posts_attributemap_bound_to_entity_map(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(_REL_PATH), json=dict(_PAIR))
            m.get(backend.url_for("entitymaps"), json={"value": [{"entitymapid": _MAP_ID}]})
            m.post(
                backend.url_for("attributemaps"),
                status_code=204,
                headers={"OData-EntityId": f"https://x/api/data/v9.2/attributemaps({_MAP_ID})"},
            )
            out = mp.create_mapping(
                backend, _REL, source_attr="name", target_attr="new_name",
            )
        assert out["created"] is True
        assert out["source_entity"] == "account"
        assert out["target_entity"] == "new_widget"
        body = [r for r in m.request_history if r.method == "POST"][0].json()
        assert body["sourceattributename"] == "name"
        assert body["targetattributename"] == "new_name"
        assert body["entitymapid@odata.bind"] == f"/entitymaps({_MAP_ID})"

    def test_solution_header_plumbed(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(_REL_PATH), json=dict(_PAIR))
            m.get(backend.url_for("entitymaps"), json={"value": [{"entitymapid": _MAP_ID}]})
            m.post(backend.url_for("attributemaps"), status_code=204)
            mp.create_mapping(
                backend, _REL, source_attr="name", target_attr="new_name",
                solution="mysol",
            )
        post = [r for r in m.request_history if r.method == "POST"][0]
        assert post.headers["MSCRM.SolutionUniqueName"] == "mysol"

    def test_missing_entity_map_raises(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(_REL_PATH), json=dict(_PAIR))
            m.get(backend.url_for("entitymaps"), json={"value": []})
            with pytest.raises(D365Error, match="No entity map"):
                mp.create_mapping(
                    backend, _REL, source_attr="name", target_attr="new_name",
                )

    def test_unknown_relationship_raises(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(_REL_PATH), json={})
            with pytest.raises(D365Error, match="one-to-many relationship"):
                mp.create_mapping(
                    backend, _REL, source_attr="name", target_attr="new_name",
                )

    def test_dry_run_resolves_but_does_not_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(_REL_PATH), json=dict(_PAIR))
            m.get(dry_backend.url_for("entitymaps"), json={"value": [{"entitymapid": _MAP_ID}]})
            m.post(dry_backend.url_for("attributemaps"), status_code=204)
            out = mp.create_mapping(
                dry_backend, _REL, source_attr="name", target_attr="new_name",
            )
        assert out["_dry_run"] is True
        assert out["would_create_mapping"] is True
        assert out["entity_map_id"] == _MAP_ID
        assert [r for r in m.request_history if r.method == "POST"] == []


class TestAutoMap:
    def test_posts_automapentity_with_pair(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(_REL_PATH), json=dict(_PAIR))
            m.post(
                backend.url_for("AutoMapEntity"),
                json={"EntityMap": {"AttributeMaps": [{"x": 1}, {"x": 2}]}},
            )
            out = mp.auto_map(backend, _REL)
        assert out["auto_mapped"] is True
        assert out["mapping_count"] == 2
        body = [r for r in m.request_history if r.method == "POST"][0].json()
        assert body["SourceEntityName"] == "account"
        assert body["TargetEntityName"] == "new_widget"

    def test_dry_run_does_not_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(_REL_PATH), json=dict(_PAIR))
            m.post(dry_backend.url_for("AutoMapEntity"), json={})
            out = mp.auto_map(dry_backend, _REL)
        assert out["_dry_run"] is True
        assert out["would_auto_map"] is True
        assert [r for r in m.request_history if r.method == "POST"] == []
