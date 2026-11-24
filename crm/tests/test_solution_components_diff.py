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
from crm.core import solution_components as sc_mod

_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _comp(ct: int, oid: str, rcb: int | None = 0) -> dict:
    return {"componenttype": ct, "objectid": oid, "rootcomponentbehavior": rcb}


class TestNormalizeComponents:
    def test_returns_exact_three_keys(self):
        raw = [
            {
                "componenttype": 1,
                "objectid": _A,
                "rootcomponentbehavior": 0,
                "extra_field": "ignored",
            }
        ]
        result = sc_mod.normalize_components(raw)
        assert len(result) == 1
        assert set(result[0].keys()) == {"componenttype", "objectid", "rootcomponentbehavior"}

    def test_componenttype_string_coerced_to_int(self):
        raw = [{"componenttype": "61", "objectid": _A, "rootcomponentbehavior": 0}]
        result = sc_mod.normalize_components(raw)
        assert result[0]["componenttype"] == 61
        assert isinstance(result[0]["componenttype"], int)

    def test_objectid_lowercased(self):
        raw = [{"componenttype": 1, "objectid": _A.upper(), "rootcomponentbehavior": 0}]
        result = sc_mod.normalize_components(raw)
        assert result[0]["objectid"] == _A.lower()

    @pytest.mark.parametrize("bad_objectid", [None, 123, ["x"]])
    def test_non_string_objectid_rejected(self, bad_objectid):
        """A malformed snapshot must fail fast, not silently coerce null to "none"."""
        raw = [{"componenttype": 1, "objectid": bad_objectid, "rootcomponentbehavior": 0}]
        with pytest.raises(ValueError, match="objectid must be a string"):
            sc_mod.normalize_components(raw)

    @pytest.mark.parametrize(
        "row",
        [
            {"componenttype": 1, "objectid": _A},
            {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": None},
        ],
    )
    def test_rootcomponentbehavior_none_stays_none(self, row):
        result = sc_mod.normalize_components([row])
        assert result[0]["rootcomponentbehavior"] is None

    def test_rootcomponentbehavior_coerced_to_int(self):
        raw = [{"componenttype": 1, "objectid": _A, "rootcomponentbehavior": "2"}]
        result = sc_mod.normalize_components(raw)
        assert result[0]["rootcomponentbehavior"] == 2
        assert isinstance(result[0]["rootcomponentbehavior"], int)

    def test_does_not_mutate_input(self):
        raw = [{"componenttype": "1", "objectid": _A.upper(), "rootcomponentbehavior": 0}]
        original_ct = raw[0]["componenttype"]
        original_oid = raw[0]["objectid"]
        sc_mod.normalize_components(raw)
        assert raw[0]["componenttype"] == original_ct
        assert raw[0]["objectid"] == original_oid

    def test_deterministic_sort_order(self):
        raw = [
            {"componenttype": 2, "objectid": _B, "rootcomponentbehavior": 0},
            {"componenttype": 1, "objectid": _C, "rootcomponentbehavior": 0},
            {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
        ]
        result = sc_mod.normalize_components(raw)
        assert result[0]["componenttype"] == 1
        assert result[0]["objectid"] == _A
        assert result[1]["componenttype"] == 1
        assert result[1]["objectid"] == _C
        assert result[2]["componenttype"] == 2

    def test_none_rcb_sorts_stably_with_int_rcb(self):
        """`None` maps to the sentinel -1 for ordering only. With the same componenttype,
        `_A` < `_B` lexicographically, so `_A`/rcb=0 (key `(1, _A, 0)`) sorts before
        `_B`/rcb=None (key `(1, _B, -1)`).
        """
        raw = [
            {"componenttype": 1, "objectid": _B, "rootcomponentbehavior": None},
            {"componenttype": 1, "objectid": _A, "rootcomponentbehavior": 0},
        ]
        result = sc_mod.normalize_components(raw)
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
        first = sc_mod.normalize_components(raw)
        second = sc_mod.normalize_components(first)
        assert first == second


class TestDiffComponents:
    def test_exact_match_returns_matches_true_empty_lists(self):
        live = [_comp(1, _A, 0), _comp(61, _B, 0)]
        expected = [_comp(1, _A, 0), _comp(61, _B, 0)]
        result = sc_mod.diff_components(live, expected)
        assert result["matches"] is True
        assert result["missing"] == []
        assert result["unexpected"] == []

    def test_missing_only(self):
        live = [_comp(1, _A, 0)]
        expected = [_comp(1, _A, 0), _comp(61, _B, 0)]
        result = sc_mod.diff_components(live, expected)
        assert result["matches"] is False
        assert len(result["missing"]) == 1
        assert result["missing"][0]["objectid"] == _B
        assert result["unexpected"] == []

    def test_unexpected_only(self):
        live = [_comp(1, _A, 0), _comp(61, _B, 0)]
        expected = [_comp(1, _A, 0)]
        result = sc_mod.diff_components(live, expected)
        assert result["matches"] is False
        assert result["missing"] == []
        assert len(result["unexpected"]) == 1
        assert result["unexpected"][0]["objectid"] == _B

    def test_differing_rootcomponentbehavior_appears_in_both_sides(self):
        live = [_comp(1, _A, rcb=0)]
        expected = [_comp(1, _A, rcb=2)]
        result = sc_mod.diff_components(live, expected)
        assert result["matches"] is False
        assert len(result["missing"]) == 1
        assert result["missing"][0]["rootcomponentbehavior"] == 2
        assert len(result["unexpected"]) == 1
        assert result["unexpected"][0]["rootcomponentbehavior"] == 0

    def test_normalize_round_trip_matches_true(self):
        """The raw rows exercise all coercions: uppercase objectid, string componenttype
        and `rootcomponentbehavior=None`. `diff_components(raw, normalize(raw))` must
        match: a coercion or lowercasing bug in `normalize_components` would break key
        matching between the two sides.
        """
        raw = [
            {"componenttype": "1", "objectid": _A.upper(), "rootcomponentbehavior": 0},
            {"componenttype": 61, "objectid": _B, "rootcomponentbehavior": None},
        ]
        result = sc_mod.diff_components(raw, sc_mod.normalize_components(raw))
        assert result["matches"] is True
        assert result["missing"] == []
        assert result["unexpected"] == []

    def test_objectid_case_insensitive_matching(self):
        live = [{"componenttype": 1, "objectid": _A.upper(), "rootcomponentbehavior": 0}]
        expected = [{"componenttype": 1, "objectid": _A.lower(), "rootcomponentbehavior": 0}]
        result = sc_mod.diff_components(live, expected)
        assert result["matches"] is True

    def test_empty_both_sides_matches_true(self):
        result = sc_mod.diff_components([], [])
        assert result["matches"] is True
        assert result["missing"] == []
        assert result["unexpected"] == []

    def test_missing_and_unexpected_are_deterministically_sorted(self):
        live = [_comp(1, _C, 0)]
        expected = [_comp(1, _A, 0), _comp(1, _B, 0)]
        result = sc_mod.diff_components(live, expected)
        assert result["missing"][0]["objectid"] == _A
        assert result["missing"][1]["objectid"] == _B

    def test_result_keys_are_exactly_three_fields(self):
        live = [_comp(1, _A, 0)]
        expected = [_comp(1, _B, 0)]
        result = sc_mod.diff_components(live, expected)
        for item in result["missing"] + result["unexpected"]:
            assert set(item.keys()) == {"componenttype", "objectid", "rootcomponentbehavior"}


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
        monkeypatch.setattr(
            "crm.core.solution.solution_components", lambda backend, name: list(_LIVE)
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())

    def test_diff_exact_match(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        expected_file = tmp_path / "expected.json"
        expected_file.write_text(
            json.dumps(sc_mod.normalize_components(_LIVE), indent=2),
            encoding="utf-8",
        )
        result = self._invoke("--diff", str(expected_file))
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["matches"] is True

    def test_diff_drift(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
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
        assert "1" in data["error"]

    def test_save_and_roundtrip_diff(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        save_file = tmp_path / "snapshot.json"
        result = self._invoke("--save", str(save_file))
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["saved"] == str(save_file)
        contents = json.loads(save_file.read_text(encoding="utf-8"))
        assert isinstance(contents, list)
        for item in contents:
            assert set(item.keys()) == {"componenttype", "objectid", "rootcomponentbehavior"}
        result2 = self._invoke("--diff", str(save_file))
        assert result2.exit_code == 0, result2.output
        data2 = json.loads(result2.output)
        assert data2["ok"] is True
        assert data2["data"]["matches"] is True

    def test_diff_and_save_mutually_exclusive(self, monkeypatch, tmp_path):
        """A usage error (exit 2), not exit 1, and the backend is never called. See ADR 0001."""
        backend_called = {"called": False}

        def fake_components(backend, name):
            backend_called["called"] = True
            return list(_LIVE)

        monkeypatch.setattr("crm.core.solution.solution_components", fake_components)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        f = tmp_path / "f.json"
        f.write_text("[]", encoding="utf-8")
        result = self._invoke("--diff", str(f), "--save", str(tmp_path / "out.json"))
        assert result.exit_code == 2, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "mutually exclusive" in data["error"].lower()
        assert backend_called["called"] is False

    def test_bare_components_unchanged(self, monkeypatch):
        self._patch(monkeypatch)
        result = self._invoke()
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) == len(_LIVE)

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

    def test_save_oserror_clean_envelope(self, monkeypatch, tmp_path):
        """A `--save` target whose parent is a regular file makes `mkdir(parents=True)`
        raise `OSError`.
        """
        self._patch(monkeypatch)
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        target = blocker / "sub" / "out.json"
        result = self._invoke("--save", str(target))
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "Could not write" in data["error"]

    def test_diff_non_utf8_file_clean_envelope(self, monkeypatch, tmp_path):
        """Invalid UTF-8 raises `UnicodeDecodeError` at read time, not at parse time."""
        self._patch(monkeypatch)
        bad_file = tmp_path / "bad4.json"
        bad_file.write_bytes(b"\xff\xfe\x00bad")
        result = self._invoke("--diff", str(bad_file))
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "Could not read" in data["error"]

    def test_json_injects_componenttypename(self, monkeypatch):
        """Bare `--json components` injects `componenttypename` per row (#627), keeps the
        raw `componenttype` and every other original key, and falls back to the integer's
        string form for an unmapped code instead of crashing.
        """
        live = [
            {
                "componenttype": 20,
                "objectid": _A,
                "rootcomponentbehavior": 0,
                "solutioncomponentid": _C,
            },
            {"componenttype": 99999, "objectid": _B, "rootcomponentbehavior": None},
        ]
        monkeypatch.setattr("crm.core.solution.solution_components", lambda backend, name: live)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._invoke()
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)["data"]
        assert rows[0]["componenttypename"] == "role"
        assert rows[0]["componenttype"] == 20
        assert rows[0]["solutioncomponentid"] == _C
        assert rows[1]["componenttypename"] == "99999"
