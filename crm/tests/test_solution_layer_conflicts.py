# pyright: basic
"""Unit tests for layer_conflicts core helper + the `solution layer-conflicts`
CLI verb (#200).

Pure-function tests need no HTTP. GUIDs are generic placeholders (no real org).
"""
from __future__ import annotations

import json
from click.testing import CliRunner
from crm.cli import cli
from crm.core import solution as sol_mod

_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _comp(ct: int, oid: str, rcb: int | None = 0) -> dict:
    return {"componenttype": ct, "objectid": oid, "rootcomponentbehavior": rcb}


class TestLayerConflicts:
    def test_overlap_returns_intersection(self):
        managed = [_comp(1, _A, 0), _comp(61, _B, 0)]
        unmanaged = [_comp(1, _A, 0), _comp(20, _C, 0)]
        result = sol_mod.layer_conflicts(managed, unmanaged)
        assert len(result) == 1
        assert result[0]["componenttype"] == 1
        assert result[0]["objectid"] == _A

    def test_no_overlap_returns_empty(self):
        managed = [_comp(1, _A, 0)]
        unmanaged = [_comp(20, _C, 0)]
        assert sol_mod.layer_conflicts(managed, unmanaged) == []

    def test_rcb_differs_still_conflicts(self):
        # Same (componenttype, objectid), different rootcomponentbehavior → still an
        # overlap; the row carries BOTH sides' rcb.
        managed = [_comp(1, _A, 0)]
        unmanaged = [_comp(1, _A, 2)]
        result = sol_mod.layer_conflicts(managed, unmanaged)
        assert len(result) == 1
        assert result[0]["managed_rootcomponentbehavior"] == 0
        assert result[0]["unmanaged_rootcomponentbehavior"] == 2

    def test_friendly_type_name(self):
        result = sol_mod.layer_conflicts([_comp(1, _A, 0)], [_comp(1, _A, 0)])
        assert result[0]["type_name"] == "entity"

    def test_unmapped_type_falls_back_to_str_int(self):
        # 9999 is not in SOLUTION_COMPONENT_TYPES.
        result = sol_mod.layer_conflicts([_comp(9999, _A, 0)], [_comp(9999, _A, 0)])
        assert result[0]["type_name"] == "9999"

    def test_sorted_by_type_then_objectid(self):
        managed = [_comp(61, _B, 0), _comp(1, _C, 0), _comp(1, _A, 0)]
        unmanaged = [_comp(61, _B, 0), _comp(1, _C, 0), _comp(1, _A, 0)]
        result = sol_mod.layer_conflicts(managed, unmanaged)
        keys = [(r["componenttype"], r["objectid"]) for r in result]
        assert keys == [(1, _A), (1, _C), (61, _B)]

    def test_case_insensitive_objectid_matching(self):
        managed = [_comp(1, _A.upper(), 0)]
        unmanaged = [_comp(1, _A.lower(), 0)]
        assert len(sol_mod.layer_conflicts(managed, unmanaged)) == 1

    def test_row_has_exact_five_keys(self):
        result = sol_mod.layer_conflicts([_comp(1, _A, 0)], [_comp(1, _A, 0)])
        assert set(result[0].keys()) == {
            "componenttype", "type_name", "objectid",
            "managed_rootcomponentbehavior", "unmanaged_rootcomponentbehavior",
        }


# ── CLI wiring ───────────────────────────────────────────────────────────────


def _info(name: str, *, managed: bool) -> dict:
    return {"uniquename": name, "solutionid": f"id-{name}", "ismanaged": managed}


class TestLayerConflictsCmd:
    """CLI wiring for `solution layer-conflicts`."""

    def _invoke(self, *args):
        return CliRunner().invoke(
            cli, ["--json", "solution", "layer-conflicts", *args]
        )

    def _patch(self, monkeypatch, *, managed_ok=True, unmanaged_ok=True,
               managed_comps=None, unmanaged_comps=None):
        infos = {
            "Mgd": _info("Mgd", managed=managed_ok),
            "Unmgd": _info("Unmgd", managed=not unmanaged_ok),
        }
        comps = {
            "Mgd": managed_comps if managed_comps is not None else [],
            "Unmgd": unmanaged_comps if unmanaged_comps is not None else [],
        }
        monkeypatch.setattr("crm.core.solution.solution_info",
                            lambda backend, name: infos[name])
        monkeypatch.setattr("crm.core.solution.solution_components",
                            lambda backend, name: list(comps[name]))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())

    def test_overlap_exit0_lists_conflicts(self, monkeypatch):
        self._patch(monkeypatch,
                    managed_comps=[_comp(1, _A, 0), _comp(61, _B, 0)],
                    unmanaged_comps=[_comp(1, _A, 0)])
        result = self._invoke("--solution", "Mgd", "--unmanaged-solution", "Unmgd")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["type_name"] == "entity"
        assert data["meta"]["count"] == 1

    def test_no_conflicts_exit0_empty_list(self, monkeypatch):
        self._patch(monkeypatch,
                    managed_comps=[_comp(1, _A, 0)],
                    unmanaged_comps=[_comp(20, _C, 0)])
        result = self._invoke("--solution", "Mgd", "--unmanaged-solution", "Unmgd")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"] == []
        assert data["meta"]["count"] == 0

    def test_no_conflicts_human_says_so(self, monkeypatch):
        self._patch(monkeypatch,
                    managed_comps=[_comp(1, _A, 0)],
                    unmanaged_comps=[_comp(20, _C, 0)])
        result = CliRunner().invoke(
            cli, ["solution", "layer-conflicts",
                  "--solution", "Mgd", "--unmanaged-solution", "Unmgd"]
        )
        assert result.exit_code == 0, result.output
        assert "no conflicts found" in result.output.lower()

    def test_solution_not_managed_exit1_names_flag(self, monkeypatch):
        self._patch(monkeypatch, managed_ok=False)
        result = self._invoke("--solution", "Mgd", "--unmanaged-solution", "Unmgd")
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "--solution" in data["error"]

    def test_unmanaged_solution_not_unmanaged_exit1_names_flag(self, monkeypatch):
        self._patch(monkeypatch, unmanaged_ok=False)
        result = self._invoke("--solution", "Mgd", "--unmanaged-solution", "Unmgd")
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "--unmanaged-solution" in data["error"]

    def test_missing_required_flags_is_usage_error(self):
        result = self._invoke("--solution", "Mgd")
        assert result.exit_code == 2, result.output
