# pyright: basic
"""Unit tests for normalize_components / diff_components (#82).

Pure-function tests — no HTTP, no backend needed.
GUIDs are generic placeholders (no real org names).
"""
from __future__ import annotations

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
