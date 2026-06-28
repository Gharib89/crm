"""CLI-seam tests for `crm solution export-spec` (#613).

The whole-solution projection (`build_solution_spec`) is exercised against a
mocked org in test_export_spec.py; here we test only the command's
output-shaping — the `-o` bare-YAML write and the `skipped`-bucket surfacing in
both human and --json modes — with `build_solution_spec` stubbed to a canned
result so the test pins the envelope contract, not the projection.
"""
# pyright: basic
from __future__ import annotations

import json

import yaml
from click.testing import CliRunner

from crm.cli import cli


def _canned(skipped=None):
    return {
        "spec": {
            "solution": {"unique_name": "myorgsln"},
            "entities": [{
                "schema_name": "new_Project",
                "display_name": "Project",
                "attributes": [
                    {"kind": "string", "schema_name": "new_Code", "display_name": "Code"},
                ],
            }],
            "optionsets": [{"name": "new_set", "display_name": "Set", "options": []}],
        },
        "skipped": skipped if skipped is not None else [
            {"type": "pluginassembly", "objectid": "p1",
             "reason": "plugin assembly DLL not projectable from a live org; "
                       "export emits only apply-seedable components."},
        ],
    }


class TestSolutionExportSpecCommand:
    def _stub(self, monkeypatch, backend, result=None):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        monkeypatch.setattr(
            "crm.core.export_spec.build_solution_spec",
            lambda *a, **k: result if result is not None else _canned(),
        )

    def test_o_writes_bare_apply_ready_yaml(self, monkeypatch, backend, tmp_path):
        self._stub(monkeypatch, backend)
        out_file = tmp_path / "spec.yaml"
        res = CliRunner().invoke(
            cli, ["--json", "solution", "export-spec", "myorgsln", "-o", str(out_file)])
        assert res.exit_code == 0, res.output

        # The file holds the BARE apply-ready spec — solution dict + entities,
        # and crucially NO `skipped` key (that would break `crm apply -f`).
        written = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert written["solution"] == {"unique_name": "myorgsln"}
        assert [e["schema_name"] for e in written["entities"]] == ["new_Project"]
        assert "skipped" not in written

        # The envelope reports counts plus the skipped bucket.
        data = json.loads(res.output)["data"]
        assert data["path"] == str(out_file)
        assert data["entities"] == 1
        assert data["attributes"] == 1
        assert data["optionsets"] == 1
        assert [s["type"] for s in data["skipped"]] == ["pluginassembly"]

    def test_no_output_emits_summary_and_skipped(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        res = CliRunner().invoke(cli, ["--json", "solution", "export-spec", "myorgsln"])
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)["data"]
        assert data["solution"] == "myorgsln"
        assert data["entities"] == ["new_Project"]
        assert data["optionsets"] == ["new_set"]
        assert data["skipped"][0]["type"] == "pluginassembly"

    def test_human_mode_surfaces_skipped(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        res = CliRunner().invoke(cli, ["solution", "export-spec", "myorgsln"])
        assert res.exit_code == 0, res.output
        assert "skipped" in res.output.lower()

    def test_unsupported_only_still_succeeds(self, monkeypatch, backend):
        # A solution with no entity-rooted members projects nothing but exits 0.
        self._stub(monkeypatch, backend, result={
            "spec": {"solution": {"unique_name": "x"}, "entities": []},
            "skipped": [{"type": "role", "objectid": "r1", "reason": "deferred."}],
        })
        res = CliRunner().invoke(cli, ["--json", "solution", "export-spec", "x"])
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)["data"]
        assert data["entities"] == []
        assert data["skipped"][0]["type"] == "role"
