# pyright: basic
"""Unit tests for normalize_components / diff_components (#82).

Pure-function tests — no HTTP, no backend needed.
GUIDs are generic placeholders (no real org names).
"""
from __future__ import annotations

import json
import pytest
from click.testing import CliRunner
from crm.cli import cli
from crm.core import solution as sol_mod

_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _comp(ct: int, oid: str, rcb: int | None = 0) -> dict:
    return {"componenttype": ct, "objectid": oid, "rootcomponentbehavior": rcb}


class TestNormalizeComponents:
    def test_returns_exact_three_keys(self):
        raw = [{"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0,
                "extra_field": "ignored"}]
        result = sol_mod.normalize_components(raw)
        assert len(result) == 1
        assert set(result[0].keys()) == {"componenttype", "objectid", "rootcomponentbehavior"}

    def test_componenttype_string_coerced_to_int(self):
        raw = [{"componenttype": "61", "objectid": _A, "rootcomponentbehavior": 0}]
        result = sol_mod.normalize_components(raw)
        assert result[0]["componenttype"] == 61
        assert isinstance(result[0]["componenttype"], int)

    def test_objectid_lowercased(self):
        raw = [{"componenttype": 1, "objectid": _A.upper(), "rootcomponentbehavior": 0}]
        result = sol_mod.normalize_components(raw)
        assert result[0]["objectid"] == _A.lower()

    @pytest.mark.parametrize("bad_objectid", [None, 123, ["x"]])
    def test_non_string_objectid_rejected(self, bad_objectid):
        # A malformed snapshot must fail fast, not silently coerce null -> "none".
        raw = [{"componenttype": 1, "objectid": bad_objectid, "rootcomponentbehavior": 0}]
        with pytest.raises(ValueError, match="objectid must be a string"):
            sol_mod.normalize_components(raw)

    def test_rootcomponentbehavior_missing_becomes_none(self):
        raw = [{"componenttype": 1, "objectid": _A}]
        result = sol_mod.normalize_components(raw)
        assert result[0]["rootcomponentbehavior"] is None

    def test_rootcomponentbehavior_none_stays_none(self):
        raw = [{"componenttype": 1, "objectid": _A, "rootcomponentbehavior": None}]
        result = sol_mod.normalize_components(raw)
        assert result[0]["rootcomponentbehavior"] is None

    def test_rootcomponentbehavior_coerced_to_int(self):
        raw = [{"componenttype": 1, "objectid": _A, "rootcomponentbehavior": "2"}]
        result = sol_mod.normalize_components(raw)
        assert result[0]["rootcomponentbehavior"] == 2
        assert isinstance(result[0]["rootcomponentbehavior"], int)

    def test_does_not_mutate_input(self):
        raw = [{"componenttype": "1", "objectid": _A.upper(), "rootcomponentbehavior": 0}]
        original_ct = raw[0]["componenttype"]
        original_oid = raw[0]["objectid"]
        sol_mod.normalize_components(raw)
        assert raw[0]["componenttype"] == original_ct
        assert raw[0]["objectid"] == original_oid

    def test_deterministic_sort_order(self):
        raw = [
            {"componenttype": 2, "objectid": _B, "rootcomponentbehavior": 0},
            {"componenttype": 1, "objectid": _C, "rootcomponentbehavior": 0},
            {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
        ]
        result = sol_mod.normalize_components(raw)
        assert result[0]["componenttype"] == 1
        assert result[0]["objectid"] == _A
        assert result[1]["componenttype"] == 1
        assert result[1]["objectid"] == _C
        assert result[2]["componenttype"] == 2

    def test_none_rcb_sorts_stably_with_int_rcb(self):
        # None maps to sentinel -1 for ordering only.
        # Same componenttype=1; _A < _B lexicographically, so _A/rcb=0 sorts first
        # (key (1, _A, 0)), then _B/rcb=None (key (1, _B, -1)).
        raw = [
            {"componenttype": 1, "objectid": _B, "rootcomponentbehavior": None},
            {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
        ]
        result = sol_mod.normalize_components(raw)
        assert len(result) == 2
        assert result[0]["objectid"] == _A
        assert result[0]["rootcomponentbehavior"] == 0
        assert result[1]["objectid"] == _B
        assert result[1]["rootcomponentbehavior"] is None

    def test_round_trip_produces_idempotent_output(self):
        raw = [
            {"componenttype": "61", "objectid": _A.upper(), "rootcomponentbehavior": 0},
            {"componenttype": 1, "objectid": _B, "rootcomponentbehavior": None},
        ]
        first = sol_mod.normalize_components(raw)
        second = sol_mod.normalize_components(first)
        assert first == second


class TestDiffComponents:
    def test_exact_match_returns_matches_true_empty_lists(self):
        live = [_comp(1, _A, 0), _comp(61, _B, 0)]
        expected = [_comp(1, _A, 0), _comp(61, _B, 0)]
        result = sol_mod.diff_components(live, expected)
        assert result["matches"] is True
        assert result["missing"] == []
        assert result["unexpected"] == []

    def test_missing_only(self):
        live = [_comp(1, _A, 0)]
        expected = [_comp(1, _A, 0), _comp(61, _B, 0)]
        result = sol_mod.diff_components(live, expected)
        assert result["matches"] is False
        assert len(result["missing"]) == 1
        assert result["missing"][0]["objectid"] == _B
        assert result["unexpected"] == []

    def test_unexpected_only(self):
        live = [_comp(1, _A, 0), _comp(61, _B, 0)]
        expected = [_comp(1, _A, 0)]
        result = sol_mod.diff_components(live, expected)
        assert result["matches"] is False
        assert result["missing"] == []
        assert len(result["unexpected"]) == 1
        assert result["unexpected"][0]["objectid"] == _B

    def test_differing_rootcomponentbehavior_appears_in_both_sides(self):
        # Same (componenttype, objectid) but different rootcomponentbehavior
        # → different tuple key → one missing, one unexpected
        live = [_comp(1, _A, rcb=0)]
        expected = [_comp(1, _A, rcb=2)]
        result = sol_mod.diff_components(live, expected)
        assert result["matches"] is False
        assert len(result["missing"]) == 1
        assert result["missing"][0]["rootcomponentbehavior"] == 2
        assert len(result["unexpected"]) == 1
        assert result["unexpected"][0]["rootcomponentbehavior"] == 0

    def test_normalize_round_trip_matches_true(self):
        # raw rows exercise all coercions: uppercase objectid, string componenttype,
        # and rootcomponentbehavior=None.  diff_components(raw, normalize(raw)) must
        # match — a coercion/lowercasing bug in normalize_components would break key
        # matching between the two sides.
        raw = [
            {"componenttype": "1", "objectid": _A.upper(), "rootcomponentbehavior": 0},
            {"componenttype": 61, "objectid": _B, "rootcomponentbehavior": None},
        ]
        result = sol_mod.diff_components(raw, sol_mod.normalize_components(raw))
        assert result["matches"] is True
        assert result["missing"] == []
        assert result["unexpected"] == []

    def test_objectid_case_insensitive_matching(self):
        # live has uppercase GUID, expected has lowercase — should still match
        live = [{"componenttype": 1, "objectid": _A.upper(), "rootcomponentbehavior": 0}]
        expected = [{"componenttype": 1, "objectid": _A.lower(), "rootcomponentbehavior": 0}]
        result = sol_mod.diff_components(live, expected)
        assert result["matches"] is True

    def test_empty_both_sides_matches_true(self):
        result = sol_mod.diff_components([], [])
        assert result["matches"] is True
        assert result["missing"] == []
        assert result["unexpected"] == []

    def test_missing_and_unexpected_are_deterministically_sorted(self):
        # live has C, expected has A and B — missing=[A, B] (sorted), unexpected=[C]
        live = [_comp(1, _C, 0)]
        expected = [_comp(1, _A, 0), _comp(1, _B, 0)]
        result = sol_mod.diff_components(live, expected)
        assert result["missing"][0]["objectid"] == _A
        assert result["missing"][1]["objectid"] == _B

    def test_result_keys_are_exactly_three_fields(self):
        live = [_comp(1, _A, 0)]
        expected = [_comp(1, _B, 0)]
        result = sol_mod.diff_components(live, expected)
        for item in result["missing"] + result["unexpected"]:
            assert set(item.keys()) == {"componenttype", "objectid", "rootcomponentbehavior"}


# ── CLI wiring tests ─────────────────────────────────────────────────────────

# Placeholder GUID (no real org names)
_Z = "ffffffff-ffff-ffff-ffff-ffffffffffff"

_LIVE = [
    {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
    {"componenttype": 61, "objectid": _B, "rootcomponentbehavior": 0},
]


class TestComponentsDiffSave:
    """CLI wiring for `solution components --diff` and `--save`."""

    def _invoke(self, *args):
        return CliRunner().invoke(cli, ["--json", "solution", "components", "Contoso", *args])

    def _patch(self, monkeypatch):
        monkeypatch.setattr("crm.core.solution.solution_components", lambda backend, name: list(_LIVE))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())

    # 1. --diff exact match → exit 0, ok True, data["matches"] True
    def test_diff_exact_match(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        expected_file = tmp_path / "expected.json"
        expected_file.write_text(
            json.dumps(sol_mod.normalize_components(_LIVE), indent=2),
            encoding="utf-8",
        )
        result = self._invoke("--diff", str(expected_file))
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["matches"] is True

    # 2. --diff with drift → exit 1, ok False, missing/unexpected non-empty, error has counts
    def test_diff_drift(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        # Expected has _A and _Z (not _B); live has _A and _B (not _Z)
        expected_items = [
            {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
            {"componenttype": 61, "objectid": _Z, "rootcomponentbehavior": 0},
        ]
        expected_file = tmp_path / "expected.json"
        expected_file.write_text(json.dumps(expected_items, indent=2), encoding="utf-8")
        result = self._invoke("--diff", str(expected_file))
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert len(data["data"]["missing"]) > 0
        assert len(data["data"]["unexpected"]) > 0
        assert "1" in data["error"]   # counts in the error message

    # 3. --save writes normalized file; --diff round-trip → exit 0, matches True
    def test_save_and_roundtrip_diff(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        save_file = tmp_path / "snapshot.json"
        result = self._invoke("--save", str(save_file))
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["saved"] == str(save_file)
        # Verify file content is a list of normalized 3-key dicts
        contents = json.loads(save_file.read_text(encoding="utf-8"))
        assert isinstance(contents, list)
        for item in contents:
            assert set(item.keys()) == {"componenttype", "objectid", "rootcomponentbehavior"}
        # Round-trip: --diff against the saved file with same live → no drift
        result2 = self._invoke("--diff", str(save_file))
        assert result2.exit_code == 0, result2.output
        data2 = json.loads(result2.output)
        assert data2["ok"] is True
        assert data2["data"]["matches"] is True

    # 4. --diff + --save together → non-zero exit, error says mutually exclusive, backend NOT called
    def test_diff_and_save_mutually_exclusive(self, monkeypatch, tmp_path):
        backend_called = {"called": False}

        def fake_components(backend, name):
            backend_called["called"] = True
            return list(_LIVE)

        monkeypatch.setattr("crm.core.solution.solution_components", fake_components)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        f = tmp_path / "f.json"
        f.write_text("[]", encoding="utf-8")
        result = self._invoke("--diff", str(f), "--save", str(tmp_path / "out.json"))
        assert result.exit_code != 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "mutually exclusive" in data["error"].lower()
        assert backend_called["called"] is False

    # 5. bare components <name> (no flags) → exit 0, data is the live list (unchanged)
    def test_bare_components_unchanged(self, monkeypatch):
        self._patch(monkeypatch)
        result = self._invoke()
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) == len(_LIVE)

    # 6. --diff with a row missing componenttype → exit 1, clean ok=False envelope
    def test_diff_malformed_missing_componenttype(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(
            json.dumps([{"objectid": _A}]),
            encoding="utf-8",
        )
        result = self._invoke("--diff", str(bad_file))
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "Malformed" in data["error"] or str(bad_file) in data["error"]

    # 7. --diff with a non-dict list element → exit 1, clean ok=False envelope
    def test_diff_malformed_non_dict_element(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        bad_file = tmp_path / "bad2.json"
        bad_file.write_text(
            json.dumps(["notadict"]),
            encoding="utf-8",
        )
        result = self._invoke("--diff", str(bad_file))
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "Malformed" in data["error"] or str(bad_file) in data["error"]

    # 8. --diff with a null objectid → exit 1, clean ok=False envelope (no "none" coercion)
    def test_diff_malformed_null_objectid(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        bad_file = tmp_path / "bad3.json"
        bad_file.write_text(
            json.dumps([{"componenttype": 1, "objectid": None, "rootcomponentbehavior": 0}]),
            encoding="utf-8",
        )
        result = self._invoke("--diff", str(bad_file))
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "Malformed" in data["error"] or str(bad_file) in data["error"]
