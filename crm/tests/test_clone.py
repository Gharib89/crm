"""Unit tests for crm.core.clone."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core.clone import retarget_spec


def _spec():
    return {
        "entities": [{
            "schema_name": "new_Project",
            "display_name": "Project",
            "display_collection_name": "Projects",
            "ownership": "UserOwned",
            "primary_attr": {"schema_name": "new_Name", "label": "Name"},
            "attributes": [
                {"kind": "string", "schema_name": "new_Code", "display_name": "Code",
                 "max_length": 100},
                {"kind": "lookup", "schema_name": "new_AccountId", "display_name": "Account",
                 "target_entity": "account"},
            ],
            "views": [{"name": "Active Projects",
                       "columns": [{"name": "new_name", "width": 200}]}],
        }],
        "optionsets": [{"name": "new_status", "display_name": "Status", "options": []}],
    }


class TestRetargetSpec:
    def test_renames_entity_schema_and_display(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone", display="Ticket Clone")
        ent = spec["entities"][0]
        assert ent["schema_name"] == "cwx_TicketClone"
        assert ent["display_name"] == "Ticket Clone"
        assert "display_collection_name" not in ent

    def test_default_display_appends_clone(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone")
        assert spec["entities"][0]["display_name"] == "Project (Clone)"

    def test_attributes_optionsets_views_untouched(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone")
        ent = spec["entities"][0]
        assert ent["attributes"][0]["schema_name"] == "new_Code"
        assert ent["attributes"][1]["kind"] == "lookup"
        assert ent["attributes"][1]["target_entity"] == "account"
        assert ent["attributes"][1]["schema_name"] == "new_AccountId"
        assert ent["primary_attr"]["schema_name"] == "new_Name"
        assert ent["views"][0]["columns"][0]["name"] == "new_name"
        assert spec["optionsets"][0]["name"] == "new_status"

    def test_does_not_invent_a_relationships_key(self):
        spec = _spec()
        retarget_spec(spec, new_schema="cwx_TicketClone")
        assert "relationships" not in spec["entities"][0]


from crm.core import clone as clone_mod


def _applied(*kinds):
    """Build an apply_spec-shaped result whose `applied` has one entry per kind."""
    applied = [{"kind": k, "name": f"{k}1"} for k in kinds]
    return {"ok": True, "applied": applied, "skipped": [], "planned": [],
            "failed": [], "staged": False}


class TestCloneEntitySkeleton:
    def _patch_common(self, monkeypatch, *, apply_result, captured):
        def fake_build(backend, logical, *, with_views=False, with_relationships=False):
            captured["with_views"] = with_views
            captured["with_relationships"] = with_relationships
            return {"entities": [{"schema_name": "new_Project", "display_name": "Project",
                                  "attributes": []}]}

        def fake_apply(backend, spec, *, solution=None, stage_only=False):
            captured["spec"] = spec
            captured["solution"] = solution
            captured["stage_only"] = stage_only
            return apply_result

        monkeypatch.setattr(clone_mod, "build_entity_spec", fake_build)
        monkeypatch.setattr(clone_mod, "apply_spec", fake_apply)

    def test_skeleton_counts_and_logical_name(self, monkeypatch):
        captured: dict = {}
        self._patch_common(
            monkeypatch,
            apply_result=_applied("entity", "attribute", "attribute", "view"),
            captured=captured)
        out = clone_mod.clone_entity(
            None, "new_project", "cwx_TicketClone", with_views=True)
        assert out["logical_name"] == "cwx_ticketclone"
        assert out["schema_name"] == "cwx_TicketClone"
        assert out["source"] == "new_project"
        assert out["counts"]["attributes"] == 2
        assert out["counts"]["views"] == 1
        assert out["counts"]["forms"] == 0
        assert out["counts"]["workflows"] == 0
        assert "relationships" not in out["counts"]
        assert "Ribbon not cloned" in out["ribbon_note"]

    def test_relationships_are_never_read(self, monkeypatch):
        captured: dict = {}
        self._patch_common(monkeypatch, apply_result=_applied("entity"), captured=captured)
        clone_mod.clone_entity(None, "new_project", "cwx_TicketClone", with_views=False)
        assert captured["with_views"] is False
        assert captured["with_relationships"] is False

    def test_no_publish_maps_to_stage_only(self, monkeypatch):
        captured: dict = {}
        self._patch_common(monkeypatch, apply_result=_applied("entity"), captured=captured)
        clone_mod.clone_entity(None, "new_project", "cwx_TicketClone", publish=False)
        assert captured["stage_only"] is True

    def test_invalid_prefix_raises_before_any_call(self, monkeypatch):
        from crm.utils.d365_backend import D365Error
        called = {"build": False}
        monkeypatch.setattr(clone_mod, "build_entity_spec",
                            lambda *a, **k: called.__setitem__("build", True))
        with pytest.raises(D365Error, match="customizationprefix"):
            clone_mod.clone_entity(None, "new_project", "mscrm_Bad")
        assert called["build"] is False

    def test_solution_threaded_to_apply(self, monkeypatch):
        captured: dict = {}
        self._patch_common(monkeypatch, apply_result=_applied("entity"), captured=captured)
        clone_mod.clone_entity(None, "new_project", "cwx_TicketClone", solution="MySol")
        assert captured["solution"] == "MySol"
