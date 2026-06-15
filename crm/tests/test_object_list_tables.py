"""Human-mode table rendering for object-list results (#306).

`solution components` and `metadata relationships` return lists of structured
objects; in human mode they must render as aligned tables (like the other list
verbs), not raw JSON strings. `--json` output must be unchanged.

Offline: the core fetch is monkeypatched and the backend stubbed, so no HTTP.
GUIDs are placeholders (no real org identifiers).
"""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli

_OID_A = "11111111-1111-1111-1111-111111111111"
_OID_B = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _stub_components(monkeypatch, items):
    monkeypatch.setattr("crm.core.solution.solution_components",
                        lambda backend, unique_name: items)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())


def _stub_relationships(monkeypatch, info):
    monkeypatch.setattr("crm.core.relationships.list_relationships",
                        lambda backend, logical_name: info)
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())


_REL_INFO = {
    "OneToMany": [{"@odata.etag": 'W/"1"', "SchemaName": "account_tasks",
                   "ReferencedEntity": "account", "ReferencingEntity": "task",
                   "ReferencingAttribute": "regardingobjectid"}],
    "ManyToOne": [{"SchemaName": "task_owner", "ReferencedEntity": "systemuser",
                   "ReferencingEntity": "task", "ReferencingAttribute": "owninguser"}],
    "ManyToMany": [{"SchemaName": "accountleads_association",
                    "Entity1LogicalName": "account", "Entity2LogicalName": "lead",
                    "IntersectEntityName": "accountleads"}],
}


class TestSolutionComponentsTable:
    def test_human_mode_renders_type_name_not_raw_json(self, monkeypatch):
        _stub_components(monkeypatch, [
            {"@odata.etag": 'W/"123"', "componenttype": 1, "objectid": _OID_A,
             "rootcomponentbehavior": 0},
        ])
        result = CliRunner().invoke(cli, ["solution", "components", "CRMWorx"])
        assert result.exit_code == 0, result.output
        # componenttype 1 resolves to its friendly name, not the bare int.
        assert "entity" in result.output
        # The objectid is shown; the raw JSON-string dump is gone.
        assert _OID_A in result.output
        assert '{"' not in result.output
        assert "@odata.etag" not in result.output

    def test_unknown_componenttype_falls_back_to_int(self, monkeypatch):
        _stub_components(monkeypatch, [
            {"componenttype": 99999, "objectid": _OID_A, "rootcomponentbehavior": 0},
        ])
        result = CliRunner().invoke(cli, ["solution", "components", "CRMWorx"])
        assert result.exit_code == 0, result.output
        assert "99999" in result.output

    def test_json_mode_unchanged(self, monkeypatch):
        items = [{"@odata.etag": 'W/"1"', "componenttype": 61, "objectid": _OID_B,
                  "rootcomponentbehavior": 0}]
        _stub_components(monkeypatch, items)
        result = CliRunner().invoke(cli, ["--json", "solution", "components", "CRMWorx"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # JSON carries the items with the numeric type, but `@odata.*` protocol
        # keys are stripped from the curated data payload (ADR 0008 / #304).
        assert payload["data"] == [{"componenttype": 61, "objectid": _OID_B,
                                    "rootcomponentbehavior": 0}]
        assert payload["meta"]["count"] == 1


class TestMetadataRelationshipsTable:
    def test_human_mode_renders_three_tables_not_raw_json(self, monkeypatch):
        _stub_relationships(monkeypatch, _REL_INFO)
        result = CliRunner().invoke(cli, ["metadata", "relationships", "account"])
        assert result.exit_code == 0, result.output
        out = result.output
        # Each category is a labeled section, not a JSON blob.
        assert "OneToMany" in out
        assert "ManyToOne" in out
        assert "ManyToMany" in out
        # Representative values from each group appear as table cells.
        assert "account_tasks" in out
        assert "task_owner" in out
        assert "accountleads_association" in out
        assert "accountleads" in out          # N:N intersect entity column
        # No raw JSON-string dump, no protocol keys as columns.
        assert '{"' not in out
        assert "@odata.etag" not in out

    def test_empty_categories_render_without_error(self, monkeypatch):
        _stub_relationships(monkeypatch, {"OneToMany": [], "ManyToOne": [],
                                          "ManyToMany": []})
        result = CliRunner().invoke(cli, ["metadata", "relationships", "account"])
        assert result.exit_code == 0, result.output
        # All three sections still render their headers, each marked empty.
        assert result.output.count("none") == 3

    def test_json_mode_unchanged(self, monkeypatch):
        _stub_relationships(monkeypatch, _REL_INFO)
        result = CliRunner().invoke(cli, ["--json", "metadata", "relationships", "account"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # Same categorized shape, but `@odata.*` protocol keys are stripped from
        # the nested rows of the curated data payload (ADR 0008 / #304).
        expected = {
            **_REL_INFO,
            "OneToMany": [{k: v for k, v in _REL_INFO["OneToMany"][0].items()
                           if k != "@odata.etag"}],
        }
        assert payload["data"] == expected
        assert payload["meta"] == {"one_to_many": 1, "many_to_one": 1, "many_to_many": 1}
