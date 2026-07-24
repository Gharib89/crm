"""Unit tests for the `solution audit` feature (#916): whole-entity / shell
buckets, required-only cascade-candidate classification, and the add-component
add-time cascade confirmation.

The pure classifier `build_audit` is backend-free (literals in, dict out); the
orchestration + RetrieveRequiredComponents wiring is exercised with
requests_mock. No live D365 server.
"""

# pyright: basic
from __future__ import annotations

import json

import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import dependencies as dep_mod
from crm.core import solution as sol_mod
from crm.core import solution_components as sc_mod

# GUIDs: hub entity (mocd_document) + two cascade-pulled entities + a webresource.
_HUB = "11111111-1111-1111-1111-111111111111"
_REQ1 = "22222222-2222-2222-2222-222222222222"
_REQ2 = "33333333-3333-3333-3333-333333333333"
_WR = "44444444-4444-4444-4444-444444444444"


class TestBuildAudit:
    """The pure classifier: components + a required-by map → audit report."""

    def _components(self):
        return sc_mod.normalize_components(
            [
                {"componenttype": 1, "objectid": _HUB, "rootcomponentbehavior": 1},
                {"componenttype": 1, "objectid": _REQ1, "rootcomponentbehavior": 0},
                {"componenttype": 1, "objectid": _REQ2, "rootcomponentbehavior": 0},
                {"componenttype": 61, "objectid": _WR, "rootcomponentbehavior": None},
            ]
        )

    def test_buckets_whole_vs_shell_entities(self):
        audit = sc_mod.build_audit(self._components(), required_by={}, names={})
        whole_ids = {e["objectid"] for e in audit["whole_entities"]}
        shell_ids = {e["objectid"] for e in audit["shell_entities"]}
        assert whole_ids == {_REQ1, _REQ2}  # behavior 0
        assert shell_ids == {_HUB}  # behavior 1
        # the webresource (non-entity) appears in neither entity bucket
        assert _WR not in whole_ids and _WR not in shell_ids

    def test_summary_counts(self):
        audit = sc_mod.build_audit(self._components(), required_by={}, names={})
        s = audit["summary"]
        assert s["total_components"] == 4
        assert s["entity_count"] == 3
        assert s["whole_entity_count"] == 2
        assert s["shell_count"] == 1
        assert s["by_type"] == {"entity": 3, "webresource": 1}

    def test_required_only_candidates_flagged(self):
        # _REQ1/_REQ2 are required by the hub entity (cascade-pulled), _HUB is not.
        required_by = {
            (1, _REQ1): ["mocd_document"],
            (1, _REQ2): ["mocd_document"],
        }
        names = {
            (1, _REQ1): {"name": "mocd_requesttype_a"},
            (1, _REQ2): {"name": "mocd_requesttype_b"},
        }
        audit = sc_mod.build_audit(self._components(), required_by=required_by, names=names)
        cand_ids = {c["objectid"] for c in audit["required_only_candidates"]}
        assert cand_ids == {_REQ1, _REQ2}
        assert audit["summary"]["required_only_count"] == 2
        first = next(c for c in audit["required_only_candidates"] if c["objectid"] == _REQ1)
        assert first["type_name"] == "entity"
        assert first["name"] == "mocd_requesttype_a"
        assert first["required_by"] == ["mocd_document"]

    def test_names_enrich_entity_buckets(self):
        names = {(1, _HUB): {"name": "mocd_document"}}
        audit = sc_mod.build_audit(self._components(), required_by={}, names=names)
        hub = next(e for e in audit["shell_entities"] if e["objectid"] == _HUB)
        assert hub["name"] == "mocd_document"
        assert hub["behavior_label"] == "shell (no subcomponents)"


def _required_url(backend, object_id, component_type=1):
    return backend.url_for(
        dep_mod.build_dependency_path(object_id, component_type, for_="required")
    )


class TestRequiredComponentIds:
    """The RetrieveRequiredComponents fetch primitive (one live GET → keys)."""

    def test_extracts_required_keys(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                _required_url(backend, _HUB),
                json={
                    "value": [
                        {"requiredcomponenttype": 1, "requiredcomponentobjectid": _REQ1},
                        {"requiredcomponenttype": 61, "requiredcomponentobjectid": _WR.upper()},
                    ]
                },
            )
            out = sol_mod.required_component_ids(backend, _HUB, 1)
        # objectids are lowercased for stable matching; component types preserved.
        assert (1, _REQ1) in {(r["componenttype"], r["objectid"]) for r in out}
        assert (61, _WR) in {(r["componenttype"], r["objectid"]) for r in out}

    def test_empty_when_no_requirements(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_required_url(backend, _HUB), json={"value": []})
            assert sol_mod.required_component_ids(backend, _HUB, 1) == []


class TestRequiredEdges:
    """`_required_edges` builds the required-by map over entity requirers.

    Exercised through ``dry_backend`` so the per-item GET fallback runs (the
    non-dry path is the same ``run_batched`` plumbing ``resolve_component_names``
    covers, plus the live e2e).
    """

    def test_maps_requirers_and_excludes_self(self, dry_backend):
        entities = [
            {"componenttype": 1, "objectid": _HUB, "rootcomponentbehavior": 1},
            {"componenttype": 1, "objectid": _REQ1, "rootcomponentbehavior": 0},
        ]
        with requests_mock.Mocker() as m:
            # HUB requires REQ1 (cascade) and — spuriously — itself (must be dropped).
            m.get(
                _required_url(dry_backend, _HUB),
                json={
                    "value": [
                        {"requiredcomponenttype": 1, "requiredcomponentobjectid": _REQ1},
                        {"requiredcomponenttype": 1, "requiredcomponentobjectid": _HUB},
                    ]
                },
            )
            m.get(_required_url(dry_backend, _REQ1), json={"value": []})
            edges = sol_mod._required_edges(dry_backend, entities)
        assert edges == {(1, _REQ1): [_HUB]}
        assert (1, _HUB) not in edges  # self-requirement excluded


class TestPreviewRequiredComponents:
    def test_aggregates_and_excludes_requested(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                _required_url(backend, _HUB),
                json={
                    "value": [
                        # a genuine cascade target …
                        {"requiredcomponenttype": 1, "requiredcomponentobjectid": _REQ1},
                        # … and the sibling being added in the same call (excluded)
                        {"requiredcomponenttype": 61, "requiredcomponentobjectid": _WR},
                    ]
                },
            )
            m.get(_required_url(backend, _WR, 61), json={"value": []})
            out = sol_mod.preview_required_components(backend, [(_HUB, 1), (_WR, 61)])
        keys = {(r["componenttype"], r["objectid"]) for r in out}
        assert keys == {(1, _REQ1)}  # _WR excluded (it is itself being added)
        assert out[0]["type_name"] == "entity"


_AUDIT_REPORT = {
    "solution": "Delta",
    "summary": {
        "total_components": 4,
        "entity_count": 3,
        "whole_entity_count": 2,
        "shell_count": 1,
        "required_only_count": 1,
        "by_type": {"entity": 3, "webresource": 1},
    },
    "whole_entities": [
        {
            "objectid": _REQ1,
            "name": "mocd_requesttype_a",
            "rootcomponentbehavior": 0,
            "behavior_label": "whole-entity (all subcomponents)",
        }
    ],
    "shell_entities": [
        {
            "objectid": _HUB,
            "name": "mocd_document",
            "rootcomponentbehavior": 1,
            "behavior_label": "shell (no subcomponents)",
        }
    ],
    "required_only_candidates": [
        {
            "componenttype": 1,
            "type_name": "entity",
            "objectid": _REQ1,
            "name": "mocd_requesttype_a",
            "rootcomponentbehavior": 0,
            "required_by": ["mocd_document"],
        }
    ],
}


class TestAuditCommand:
    def test_audit_json(self, monkeypatch):
        monkeypatch.setattr("crm.core.solution.audit_solution", lambda backend, name: _AUDIT_REPORT)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, ["--json", "solution", "audit", "Delta"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["summary"]["whole_entity_count"] == 2
        assert payload["data"]["required_only_candidates"][0]["required_by"] == ["mocd_document"]

    def test_audit_human_surfaces_culprit_and_counts(self, monkeypatch):
        monkeypatch.setattr("crm.core.solution.audit_solution", lambda backend, name: _AUDIT_REPORT)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = CliRunner().invoke(cli, ["solution", "audit", "Delta"])
        assert result.exit_code == 0, result.output
        # the hub culprit that pulled the cascade, the pulled entity, and a count
        assert "mocd_document" in result.output
        assert "mocd_requesttype_a" in result.output
        assert "whole-entity" in result.output


def _cascade_argv(*extra):
    return ["solution", "add-component", "--solution", "Delta", *extra]


class TestAddComponentCascadeGate:
    """Add-time cascade confirmation (#916). The gate is interactive-only; --json /
    non-TTY / --dry-run fall through to the historical no-prompt behavior.
    """

    def _boom_preview(self, monkeypatch):
        def boom(backend, comps):
            raise AssertionError("preview_required_components should not run")

        monkeypatch.setattr("crm.core.solution.preview_required_components", boom)

    def _stub_add(self, monkeypatch, called):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr(
            "crm.core.solution.add_solution_component",
            lambda backend, **kw: called.update(add=True) or {"added": True},
        )

    def test_interactive_decline_aborts_before_add(self, monkeypatch):
        called = {"add": False}
        self._stub_add(monkeypatch, called)
        monkeypatch.setattr(
            "crm.core.solution.preview_required_components",
            lambda backend, comps: [{"componenttype": 1, "type_name": "entity", "objectid": _REQ1}],
        )
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(
            cli, _cascade_argv("--type", "entity", "--id", _HUB), input="n\n"
        )
        assert result.exit_code == 1, result.output
        assert "This will also add 1 required component" in result.output
        assert called["add"] is False

    def test_interactive_confirm_proceeds(self, monkeypatch):
        called = {"add": False}
        self._stub_add(monkeypatch, called)
        monkeypatch.setattr(
            "crm.core.solution.preview_required_components",
            lambda backend, comps: [{"componenttype": 1, "type_name": "entity", "objectid": _REQ1}],
        )
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(
            cli, _cascade_argv("--type", "entity", "--id", _HUB), input="y\n"
        )
        assert result.exit_code == 0, result.output
        assert called["add"] is True

    def test_preview_failure_falls_through(self, monkeypatch):
        """A RetrieveRequiredComponents failure must not block the add — the
        preview is best-effort; the gate swallows D365Error and proceeds.
        """
        from crm.utils.d365_backend import D365Error

        called = {"add": False}
        self._stub_add(monkeypatch, called)

        def boom(backend, comps):
            raise D365Error("transient 503")

        monkeypatch.setattr("crm.core.solution.preview_required_components", boom)
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(cli, _cascade_argv("--type", "entity", "--id", _HUB))
        assert result.exit_code == 0, result.output
        assert called["add"] is True

    def test_yes_skips_prompt_and_preview(self, monkeypatch):
        called = {"add": False}
        self._stub_add(monkeypatch, called)
        self._boom_preview(monkeypatch)
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(cli, _cascade_argv("--type", "entity", "--id", _HUB, "--yes"))
        assert result.exit_code == 0, result.output
        assert called["add"] is True

    def test_json_mode_falls_through(self, monkeypatch):
        called = {"add": False}
        self._stub_add(monkeypatch, called)
        self._boom_preview(monkeypatch)
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(
            cli, ["--json", *_cascade_argv("--type", "entity", "--id", _HUB)]
        )
        assert result.exit_code == 0, result.output
        assert called["add"] is True

    def test_non_tty_falls_through(self, monkeypatch):
        called = {"add": False}
        self._stub_add(monkeypatch, called)
        self._boom_preview(monkeypatch)
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: False)
        result = CliRunner().invoke(cli, _cascade_argv("--type", "entity", "--id", _HUB))
        assert result.exit_code == 0, result.output
        assert called["add"] is True

    def test_no_add_required_skips_gate(self, monkeypatch):
        called = {"add": False}
        self._stub_add(monkeypatch, called)
        self._boom_preview(monkeypatch)  # gate must not preview when cascade is off
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(
            cli, _cascade_argv("--type", "entity", "--id", _HUB, "--no-add-required")
        )
        assert result.exit_code == 0, result.output
        assert called["add"] is True

    def test_batch_interactive_decline_aborts(self, monkeypatch):
        called = {"add": False}
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        monkeypatch.setattr(
            "crm.core.solution.add_solution_components",
            lambda backend, **kw: called.update(add=True) or {"count": 2, "failed": 0},
        )
        monkeypatch.setattr(
            "crm.core.solution.preview_required_components",
            lambda backend, comps: [{"componenttype": 1, "type_name": "entity", "objectid": _REQ1}],
        )
        monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
        result = CliRunner().invoke(
            cli, _cascade_argv("--type", "entity", "--id", _HUB, "--id", _REQ2), input="n\n"
        )
        assert result.exit_code == 1, result.output
        assert called["add"] is False
