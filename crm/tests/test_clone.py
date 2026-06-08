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


class TestCloneEntityForms:
    def _patch(self, monkeypatch, *, forms_list, captured):
        monkeypatch.setattr(clone_mod, "build_entity_spec",
                            lambda b, s, **k: {"entities": [{"schema_name": "new_Project",
                                                             "display_name": "Project",
                                                             "relationships": []}]})
        monkeypatch.setattr(clone_mod, "apply_spec",
                            lambda b, spec, **k: _applied("entity"))
        monkeypatch.setattr(clone_mod, "read_entity_forms", lambda b, s: forms_list)

        def fake_clone_form(backend, form, new_entity, *, solution=None):
            captured.setdefault("targets", []).append((form["name"], new_entity, solution))
            return {"created": True, "formid": "f", "name": form["name"]}

        monkeypatch.setattr(clone_mod, "clone_form_to_entity", fake_clone_form)
        monkeypatch.setattr(clone_mod, "publish_all",
                            lambda b: captured.__setitem__("published", True))

    def test_with_forms_clones_each_form_and_counts(self, monkeypatch):
        captured: dict = {}
        self._patch(monkeypatch, forms_list=[{"name": "A", "objecttypecode": "new_project"},
                                             {"name": "B", "objecttypecode": "new_project"}],
                    captured=captured)
        out = clone_mod.clone_entity(None, "new_project", "cwx_TicketClone",
                                     with_forms=True, solution="MySol")
        assert out["counts"]["forms"] == 2
        assert captured["targets"] == [("A", "cwx_ticketclone", "MySol"),
                                       ("B", "cwx_ticketclone", "MySol")]
        assert captured.get("published") is True

    def test_without_forms_does_not_read_forms(self, monkeypatch):
        captured: dict = {}
        called = {"read": False}
        monkeypatch.setattr(clone_mod, "build_entity_spec",
                            lambda b, s, **k: {"entities": [{"schema_name": "new_Project",
                                                             "display_name": "Project",
                                                             "relationships": []}]})
        monkeypatch.setattr(clone_mod, "apply_spec", lambda b, spec, **k: _applied("entity"))
        monkeypatch.setattr(clone_mod, "read_entity_forms",
                            lambda b, s: called.__setitem__("read", True) or [])
        out = clone_mod.clone_entity(None, "new_project", "cwx_TicketClone", with_forms=False)
        assert called["read"] is False
        assert out["counts"]["forms"] == 0

    def test_failed_skeleton_skips_forms(self, monkeypatch):
        captured: dict = {}
        called = {"read": False}
        self._patch(monkeypatch, forms_list=[{"name": "A", "objecttypecode": "new_project"}],
                    captured=captured)
        monkeypatch.setattr(clone_mod, "apply_spec", lambda b, spec, **k: {
            "ok": False, "applied": [{"kind": "entity", "name": "e"}],
            "skipped": [], "planned": [], "failed": [{"kind": "attribute", "name": "x"}],
            "staged": False})
        monkeypatch.setattr(clone_mod, "read_entity_forms",
                            lambda b, s: called.__setitem__("read", True) or [])
        out = clone_mod.clone_entity(None, "new_project", "cwx_TicketClone", with_forms=True)
        assert out["created"] is False
        assert called["read"] is False
        assert out["counts"]["forms"] == 0


class TestCloneEntityWorkflows:
    def _base_patch(self, monkeypatch):
        monkeypatch.setattr(clone_mod, "build_entity_spec",
                            lambda b, s, **k: {"entities": [{"schema_name": "new_Project",
                                                             "display_name": "Project",
                                                             "relationships": []}]})
        monkeypatch.setattr(clone_mod, "apply_spec", lambda b, spec, **k: _applied("entity"))

    def test_clones_supported_workflows(self, monkeypatch):
        self._base_patch(monkeypatch)
        monkeypatch.setattr(clone_mod, "list_workflows",
                            lambda b, **k: [{"workflowid": "w1", "name": "WF1"}])
        seen = {}
        monkeypatch.setattr(clone_mod, "clone_workflow_to_entity",
                            lambda b, wid, ent, **k: seen.update(wid=wid, ent=ent, sol=k.get("solution"))
                            or {"workflow_id": "new"})
        out = clone_mod.clone_entity(None, "new_project", "cwx_TicketClone",
                                     with_workflows=True, solution="MySol")
        assert out["counts"]["workflows"] == 1
        assert seen == {"wid": "w1", "ent": "cwx_ticketclone", "sol": "MySol"}

    def test_unsupported_workflow_is_skipped_not_fatal(self, monkeypatch):
        from crm.utils.d365_backend import D365Error
        self._base_patch(monkeypatch)
        monkeypatch.setattr(clone_mod, "list_workflows",
                            lambda b, **k: [{"workflowid": "w1", "name": "Good"},
                                            {"workflowid": "w2", "name": "BadAction"}])

        def fake_clone(b, wid, ent, **k):
            if wid == "w2":
                raise D365Error("Cloning category 3 (action/BPF) is not yet supported")
            return {"workflow_id": "new"}

        monkeypatch.setattr(clone_mod, "clone_workflow_to_entity", fake_clone)
        out = clone_mod.clone_entity(None, "new_project", "cwx_TicketClone", with_workflows=True)
        assert out["counts"]["workflows"] == 1
        assert len(out["skipped_workflows"]) == 1
        assert out["skipped_workflows"][0]["name"] == "BadAction"
        assert "not yet supported" in out["skipped_workflows"][0]["reason"]


from click.testing import CliRunner


class TestCloneCommand:
    def test_clone_entity_command_invokes_core(self, monkeypatch):
        from crm.commands import metadata as md_cmd

        called = {}

        def fake_clone(backend, source, new_schema, **kw):
            called.update(dict(source=source, new_schema=new_schema, **kw))
            return {"created": True, "logical_name": new_schema.lower(),
                    "counts": {"attributes": 1, "views": 0, "forms": 0, "workflows": 0},
                    "skipped_workflows": [], "ribbon_note": "n/a"}

        monkeypatch.setattr(md_cmd.clone_mod, "clone_entity", fake_clone)

        from crm.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, [
            "metadata", "clone-entity", "new_project", "cwx_TicketClone",
            "--display", "Ticket Clone", "--with-all",
        ], env={
            "D365_URL": "https://crm.contoso.local/contoso",
            "D365_USERNAME": "alice", "D365_PASSWORD": "pw", "D365_DOMAIN": "CONTOSO",
        })
        assert result.exit_code == 0, result.output
        assert called["source"] == "new_project"
        assert called["new_schema"] == "cwx_TicketClone"
        assert called["display"] == "Ticket Clone"
        assert called["with_forms"] is True
        assert called["with_views"] is True
        assert called["with_workflows"] is True

    def test_with_all_overrides_individual_flags(self, monkeypatch):
        from crm.commands import metadata as md_cmd
        called = {}
        monkeypatch.setattr(md_cmd.clone_mod, "clone_entity",
                            lambda b, s, n, **kw: called.update(kw) or {
                                "created": True, "logical_name": n.lower(),
                                "counts": {"attributes": 0, "views": 0,
                                           "forms": 0, "workflows": 0},
                                "skipped_workflows": [], "ribbon_note": "n/a"})
        from crm.cli import cli
        _env = {"D365_URL": "https://crm.contoso.local/contoso",
                "D365_USERNAME": "alice", "D365_PASSWORD": "pw", "D365_DOMAIN": "CONTOSO"}
        result = CliRunner().invoke(cli, [
            "metadata", "clone-entity", "new_project", "cwx_TicketClone", "--with-all"],
            env=_env)
        assert result.exit_code == 0, result.output
        assert called["with_forms"] and called["with_views"] and called["with_workflows"]

    def test_skipped_workflows_surface_in_output(self, monkeypatch):
        from crm.commands import metadata as md_cmd
        monkeypatch.setattr(md_cmd.clone_mod, "clone_entity",
                            lambda b, s, n, **kw: {
                                "created": True, "logical_name": n.lower(),
                                "counts": {"attributes": 0, "views": 0,
                                           "forms": 0, "workflows": 1},
                                "skipped_workflows": [{"name": "BadAction",
                                                       "reason": "not yet supported"}],
                                "ribbon_note": "n/a"})
        from crm.cli import cli
        _env = {"D365_URL": "https://crm.contoso.local/contoso",
                "D365_USERNAME": "alice", "D365_PASSWORD": "pw", "D365_DOMAIN": "CONTOSO"}
        result = CliRunner().invoke(cli, [
            "metadata", "clone-entity", "new_project", "cwx_TicketClone", "--with-workflows"],
            env=_env)
        assert result.exit_code == 0, result.output
        assert "BadAction" in result.output
