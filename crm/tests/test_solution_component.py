"""Unit tests for crm.core.solution add/remove_solution_component (#71).

The AddSolutionComponent / RemoveSolutionComponent Web API action contract
(unbound actions; add takes ComponentId, remove takes a SolutionComponent
entity reference — see #181) and the `componenttype` global optionset integer
values are verified against the Dataverse Web API docs.
All HTTP is mocked via requests_mock; no live D365 server.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod
from crm.utils.d365_backend import D365Error


_SOL_ID = "22222222-2222-2222-2222-222222222222"
_COMP_ID = "33333333-3333-3333-3333-333333333333"


def _posts(m):
    return [r for r in m.request_history if r.method == "POST"]


def _mock_solution(m, backend, *, managed: bool):
    """Mock the force-real solution_info GET with the given managed flag."""
    m.get(backend.url_for("solutions"),
          json={"value": [{"solutionid": _SOL_ID, "uniquename": "CRMWorx",
                           "ismanaged": managed}]})


class TestResolveComponentType:
    def test_int_passthrough(self):
        assert sol_mod.resolve_component_type(61) == 61

    def test_numeric_string_passthrough(self):
        assert sol_mod.resolve_component_type("61") == 61

    @pytest.mark.parametrize("name,expected", [
        ("entity", 1),
        ("attribute", 2),
        ("relationship", 3),          # canonical: base relationship (not 10)
        ("optionset", 9),
        ("entityrelationship", 10),   # canonical: entity relationship
        ("webresource", 61),
    ])
    def test_canonical_names(self, name, expected):
        assert sol_mod.resolve_component_type(name) == expected

    @pytest.mark.parametrize("variant", ["WebResource", "web resource", "web-resource",
                                         "WEB_RESOURCE", " webresource "])
    def test_name_normalized(self, variant):
        assert sol_mod.resolve_component_type(variant) == 61

    def test_unknown_name_raises(self):
        with pytest.raises(D365Error, match="component type"):
            sol_mod.resolve_component_type("nonsense")


class TestAddSolutionComponent:
    def test_posts_expected_body(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(backend.url_for("AddSolutionComponent"), status_code=204)
            out = sol_mod.add_solution_component(
                backend, solution="CRMWorx", component_id=_COMP_ID, component_type=1)
        assert out["added"] is True
        assert out["solution"] == "CRMWorx"
        body = _posts(m)[0].json()
        assert body["ComponentId"] == _COMP_ID
        assert body["ComponentType"] == 1
        assert body["SolutionUniqueName"] == "CRMWorx"
        assert body["AddRequiredComponents"] is True          # default on
        assert body["DoNotIncludeSubcomponents"] is False     # default include

    def test_flags_flip_body(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(backend.url_for("AddSolutionComponent"), status_code=204)
            sol_mod.add_solution_component(
                backend, solution="CRMWorx", component_id=_COMP_ID, component_type=61,
                add_required_components=False, do_not_include_subcomponents=True)
        body = _posts(m)[0].json()
        assert body["AddRequiredComponents"] is False
        assert body["DoNotIncludeSubcomponents"] is True

    def test_refuses_managed_no_post(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=True)
            with pytest.raises(D365Error, match="managed"):
                sol_mod.add_solution_component(
                    backend, solution="CRMWorx", component_id=_COMP_ID, component_type=1)
            assert _posts(m) == []

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, dry_backend, managed=False)
            out = sol_mod.add_solution_component(
                dry_backend, solution="CRMWorx", component_id=_COMP_ID, component_type=1)
        assert out["_dry_run"] is True
        assert "added" not in out
        assert _posts(m) == []


class TestRemoveSolutionComponent:
    def test_posts_expected_body(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=False)
            m.post(backend.url_for("RemoveSolutionComponent"), status_code=204)
            out = sol_mod.remove_solution_component(
                backend, solution="CRMWorx", component_id=_COMP_ID, component_type=61)
        assert out["removed"] is True
        assert out["solution"] == "CRMWorx"
        body = _posts(m)[0].json()
        # RemoveSolutionComponent takes a SolutionComponent entity reference —
        # the component objectid goes in as solutioncomponentid (live-verified
        # contract, #181); there is no ComponentId parameter on this action.
        assert body["SolutionComponent"] == {
            "solutioncomponentid": _COMP_ID,
            "@odata.type": "Microsoft.Dynamics.CRM.solutioncomponent",
        }
        assert body["ComponentType"] == 61
        assert body["SolutionUniqueName"] == "CRMWorx"
        assert "ComponentId" not in body

    def test_refuses_managed_no_post(self, backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, backend, managed=True)
            with pytest.raises(D365Error, match="managed"):
                sol_mod.remove_solution_component(
                    backend, solution="CRMWorx", component_id=_COMP_ID, component_type=61)
            assert _posts(m) == []

    def test_dry_run_previews_no_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            _mock_solution(m, dry_backend, managed=False)
            out = sol_mod.remove_solution_component(
                dry_backend, solution="CRMWorx", component_id=_COMP_ID, component_type=61)
        assert out["_dry_run"] is True
        assert "removed" not in out
        assert _posts(m) == []


# ── command wiring + exit codes ──────────────────────────────────────────────


_GUID = "33333333-3333-3333-3333-333333333333"


class TestComponentCommands:
    def test_add_component_resolves_name_and_wires_core(self, monkeypatch):
        captured = {}

        def fake(backend, **kw):
            captured.update(kw)
            return {"added": True, "solution": kw["solution"]}

        monkeypatch.setattr("crm.core.solution.add_solution_component", fake)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "add-component",
            "--solution", "CRMWorx", "--type", "webresource", "--id", _GUID,
        ])
        assert result.exit_code == 0, result.output
        assert captured["solution"] == "CRMWorx"
        assert captured["component_id"] == _GUID
        assert captured["component_type"] == 61          # resolved name -> int
        assert captured["add_required_components"] is True
        assert captured["do_not_include_subcomponents"] is False

    def test_add_component_int_type_and_flags(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("crm.core.solution.add_solution_component",
                            lambda backend, **kw: captured.update(kw) or {"added": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "add-component",
            "--solution", "CRMWorx", "--type", "61", "--id", _GUID,
            "--no-add-required", "--no-subcomponents",
        ])
        assert result.exit_code == 0, result.output
        assert captured["component_type"] == 61
        assert captured["add_required_components"] is False
        assert captured["do_not_include_subcomponents"] is True

    def test_add_component_entity_emits_required_components_note(self, monkeypatch):
        monkeypatch.setattr("crm.core.solution.add_solution_component",
                            lambda backend, **kw: {"added": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "add-component",
            "--solution", "CRMWorx", "--type", "entity", "--id", _GUID,
        ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "required components" in payload["meta"]["note"]

    @pytest.mark.parametrize("argv_extra", [
        ["--type", "entity", "--no-add-required"],   # entity but required-add off
        ["--type", "webresource"],                   # non-entity type
    ])
    def test_add_component_no_note_when_not_entity_with_required(self, monkeypatch,
                                                                 argv_extra):
        monkeypatch.setattr("crm.core.solution.add_solution_component",
                            lambda backend, **kw: {"added": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "add-component",
            "--solution", "CRMWorx", "--id", _GUID, *argv_extra,
        ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "note" not in payload.get("meta", {})

    def test_add_component_unknown_type_exit_1(self, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "add-component",
            "--solution", "CRMWorx", "--type", "nonsense", "--id", _GUID,
        ])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["ok"] is False

    def test_remove_component_no_yes_non_tty_aborts(self, monkeypatch):
        called = {"core": False}
        monkeypatch.setattr("crm.core.solution.remove_solution_component",
                            lambda backend, **kw: called.update(core=True) or {"removed": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "remove-component",
            "--solution", "CRMWorx", "--type", "61", "--id", _GUID,
        ], input="\n")
        assert result.exit_code == 1, result.output
        # output carries the confirm prompt before the envelope; match the substring
        assert '"error": "aborted by user"' in result.output
        assert called["core"] is False                   # gated before the core call

    def test_remove_component_yes_wires_core(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("crm.core.solution.remove_solution_component",
                            lambda backend, **kw: captured.update(kw) or {"removed": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, [
            "--json", "solution", "remove-component",
            "--solution", "CRMWorx", "--type", "webresource", "--id", _GUID, "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert captured["component_type"] == 61
        assert captured["component_id"] == _GUID
