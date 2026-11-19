# pyright: basic
"""`crm data compare` (#1342): an offline comparison of two row files by key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import export as export_mod
from crm.utils.d365_backend import D365Backend

_G1 = "11111111-1111-1111-1111-111111111111"
_G2 = "22222222-2222-2222-2222-222222222222"
_OWNER = "33333333-3333-3333-3333-333333333333"


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _compare(*args: str) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(cli, ["--json", "data", "compare", *args])
    return result.exit_code, json.loads(result.output)


def test_counts_matched_missing_extra_mismatched_and_ambiguous(tmp_path: Path) -> None:
    a = _jsonl(
        tmp_path / "a.jsonl",
        [
            {"num": "1", "name": "Acme", "city": "Cairo"},
            {"num": "2", "name": "Beta", "city": "Giza"},
            {"num": "3", "name": "Gone", "city": "Luxor"},
            {"num": "4", "name": "Dup1", "city": "X"},
            {"num": "4", "name": "Dup2", "city": "X"},
        ],
    )
    b = _jsonl(
        tmp_path / "b.jsonl",
        [
            {"num": "1", "name": "Acme", "city": "Cairo"},
            {"num": "2", "name": "Beta", "city": "Aswan"},
            {"num": "4", "name": "Dup1", "city": "X"},
            {"num": "5", "name": "New", "city": "Y"},
        ],
    )

    code, out = _compare(str(a), str(b), "--key", "num")

    assert code == 0
    d = out["data"]
    assert (d["a"]["rows"], d["b"]["rows"]) == (5, 4)
    assert d["matched"] == 2
    assert (d["missing_in_b"], d["extra_in_b"]) == (1, 1)
    assert d["mismatched"] == 1
    assert d["ambiguous"] == 1
    assert d["field_mismatches"] == {"city": 1}
    assert d["columns"] == ["city", "name"]
    assert d["samples"]["missing_in_b"] == ["3"]
    assert d["samples"]["extra_in_b"] == ["5"]
    assert d["samples"]["ambiguous"] == [{"key": "4", "a": 2, "b": 1}]
    assert d["samples"]["mismatched"] == [
        {"key": "2", "fields": [{"field": "city", "a": "Giza", "b": "Aswan"}]}
    ]
    assert d["equal"] is False


def test_composite_key(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", "region,code,amount\nN,1,10\nS,1,20\n")
    b = _csv(tmp_path / "b.csv", "region,code,amount\nN,1,10\nS,1,25\nS,2,5\n")

    code, out = _compare(str(a), str(b), "--key", "region,code")

    d = out["data"]
    assert code == 0
    assert (d["matched"], d["mismatched"], d["extra_in_b"]) == (2, 1, 1)
    assert d["samples"]["extra_in_b"] == [{"region": "S", "code": "2"}]
    assert d["samples"]["mismatched"][0]["key"] == {"region": "S", "code": "1"}


def test_column_map_selection_and_totals(tmp_path: Path) -> None:
    a = _csv(
        tmp_path / "legacy.csv",
        "AccountNumber,AccountName,Revenue,Note\nA1,Acme,100.50,x\nA2,Beta,200,y\n",
    )
    b = _jsonl(
        tmp_path / "target.jsonl",
        [
            {"accountnumber": "A1", "name": "Acme", "revenue": 100.5, "Note": "changed"},
            {"accountnumber": "A2", "name": "Beta", "revenue": 199, "Note": "y"},
        ],
    )

    code, out = _compare(
        str(a),
        str(b),
        "--key",
        "AccountNumber",
        "--map",
        "AccountNumber=accountnumber",
        "--map",
        "AccountName=name",
        "--map",
        "Revenue=revenue",
        "--columns",
        "AccountName,Revenue",
        "--total",
        "Revenue",
    )

    d = out["data"]
    assert code == 0
    assert d["columns"] == ["AccountName", "Revenue"]
    assert d["matched"] == 2
    assert d["field_mismatches"] == {"Revenue": 1}
    assert d["totals"] == {"Revenue": {"a": "300.50", "b": "299.5", "equal": False}}


def test_number_text_equals_number(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", "id,qty,price\n1,10,1.50\n")
    b = _jsonl(tmp_path / "b.jsonl", [{"id": 1, "qty": 10, "price": 1.5}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["mismatched"] == 0
    assert out["data"]["matched"] == 1


def test_number_text_with_padding_stays_verbatim(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", "id,code\n1, 10\n")
    b = _jsonl(tmp_path / "b.jsonl", [{"id": 1, "code": 10}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"code": 1}


def test_date_time_offset_compares_as_utc_instant(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "when": "2024-03-01T12:00:00+02:00"}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "when": "2024-03-01T10:00:00Z"}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["mismatched"] == 0


def test_date_time_a_different_instant_differs(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "when": "2024-03-01T12:00:00+02:00"}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "when": "2024-03-01T12:00:00Z"}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"when": 1}


def test_empty_string_equals_null(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", "id,phone\n1,\n")
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "phone": None}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["mismatched"] == 0


def test_text_is_compared_verbatim(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "name": "Acme "}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "name": "acme"}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"name": 1}


def test_lookup_value_column_compares_by_guid(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", f"id,_ownerid_value\n1,{_OWNER.upper()}\n")
    b = _jsonl(
        tmp_path / "b.jsonl",
        [
            {
                "id": "1",
                "_ownerid_value": _OWNER,
                "_ownerid_value@Microsoft.Dynamics.CRM.lookuplogicalname": "systemuser",
            }
        ],
    )

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["columns"] == ["ownerid"]
    assert out["data"]["mismatched"] == 0


def test_lookup_bind_form_compares_with_value_form(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "_ownerid_value": _OWNER}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "ownerid@odata.bind": f"/systemusers({_OWNER})"}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["columns"] == ["ownerid"]
    assert out["data"]["mismatched"] == 0


def test_lookup_bound_to_another_record_differs(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "_ownerid_value": _OWNER}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "ownerid@odata.bind": f"/systemusers({_G1})"}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"ownerid": 1}


def test_differences_exit_0_by_default_and_1_with_fail_on_diff(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "v": "x"}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "v": "y"}])

    assert _compare(str(a), str(b), "--key", "id")[0] == 0
    code, out = _compare(str(a), str(b), "--key", "id", "--fail-on-diff")
    assert code == 1
    assert out["ok"] is False
    assert out["data"]["mismatched"] == 1


def test_equal_files_exit_0_with_fail_on_diff(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "v": "x"}])

    code, out = _compare(str(a), str(a), "--key", "id", "--fail-on-diff")

    assert code == 0
    assert out["data"]["equal"] is True


def test_unknown_key_column_is_a_usage_error(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1"}])

    result = CliRunner().invoke(cli, ["data", "compare", str(a), str(a), "--key", "nope"])

    assert result.exit_code == 2
    assert "nope" in result.output


def test_unreadable_input_is_an_error(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1"}])
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n", encoding="utf-8")

    code, out = _compare(str(a), str(bad), "--key", "id")

    assert code == 1
    assert out["ok"] is False


def test_full_report_goes_to_out_file_and_stdout_stays_capped(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": str(i)} for i in range(50)])
    b = _jsonl(tmp_path / "b.jsonl", [])
    report = tmp_path / "report.json"

    _, out = _compare(str(a), str(b), "--key", "id", "--out", str(report))

    d = out["data"]
    assert d["missing_in_b"] == 50
    assert len(d["samples"]["missing_in_b"]) == 20
    assert d["out"] == str(report)
    full = json.loads(report.read_text(encoding="utf-8"))
    assert len(full["missing_in_b"]) == 50
    assert full["summary"]["missing_in_b"] == 50


def _export(backend: D365Backend, out: Path, rows: list[dict], fmt: str) -> None:
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("accounts"), json={"value": rows})
        export_mod.export_records(backend, "accounts", out, fmt=fmt, include_lookups=True)


_EXPORT_ROWS = [
    {
        "@odata.etag": 'W/"1"',
        "accountid": _G1,
        "name": "Acme",
        "revenue": 100.5,
        "donotemail": True,
        "modifiedon": "2024-03-01T10:00:00Z",
        "_ownerid_value": _OWNER,
        "_ownerid_value@Microsoft.Dynamics.CRM.lookuplogicalname": "systemuser",
    },
    {
        "@odata.etag": 'W/"2"',
        "accountid": _G2,
        "name": "Beta",
        "revenue": None,
        "donotemail": False,
        "modifiedon": "2024-03-02T10:00:00Z",
        "_ownerid_value": _OWNER,
        "_ownerid_value@Microsoft.Dynamics.CRM.lookuplogicalname": "systemuser",
    },
]


def test_export_jsonl_round_trip_compares_equal_to_itself(
    backend: D365Backend, tmp_path: Path
) -> None:
    out = tmp_path / "accounts.jsonl"
    _export(backend, out, _EXPORT_ROWS, "jsonl")

    code, result = _compare(str(out), str(out), "--key", "accountid", "--fail-on-diff")

    d = result["data"]
    assert code == 0
    assert d["equal"] is True
    assert d["matched"] == 2
    assert d["columns"] == ["donotemail", "modifiedon", "name", "ownerid", "revenue"]


def test_each_export_format_compares_equal_to_the_jsonl_export(
    backend: D365Backend, tmp_path: Path
) -> None:
    jsonl = tmp_path / "accounts.jsonl"
    _export(backend, jsonl, _EXPORT_ROWS, "jsonl")
    for fmt in ("csv", "json"):
        other = tmp_path / f"accounts.{fmt}"
        _export(backend, other, _EXPORT_ROWS, fmt)

        code, result = _compare(str(other), str(jsonl), "--key", "accountid", "--fail-on-diff")

        assert code == 0, (fmt, result)
        assert result["data"]["matched"] == 2
        assert result["data"]["columns"] == [
            "donotemail",
            "modifiedon",
            "name",
            "ownerid",
            "revenue",
        ]


def test_leading_zero_code_stays_text_and_never_pairs_with_the_number(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", "code,note\n007,x\n")
    b = _csv(tmp_path / "b.csv", "code,note\n7,x\n")

    _, out = _compare(str(a), str(b), "--key", "code")

    assert out["data"]["matched"] == 0
    assert out["data"]["missing_in_b"] == 1
    assert out["data"]["extra_in_b"] == 1


def test_unbalanced_brace_guid_is_text(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "ref": "{" + _G1}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "ref": _G1}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"ref": 1}


def test_bind_path_text_in_a_plain_column_stays_verbatim(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "note": f"/accounts({_G1})"}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "note": _G1}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"note": 1}


def test_two_columns_reading_as_one_is_an_input_error(tmp_path: Path) -> None:
    a = _jsonl(
        tmp_path / "a.jsonl",
        [{"id": "1", "_x_value": _G1, "x@odata.bind": f"/accounts({_G1})"}],
    )

    code, out = _compare(str(a), str(a), "--key", "id")

    assert code == 1
    assert "both read as 'x'" in out["error"]


def test_unknown_key_against_an_empty_csv_with_a_header_is_a_usage_error(
    tmp_path: Path,
) -> None:
    a = _csv(tmp_path / "a.csv", "id,name\n")

    result = CliRunner().invoke(cli, ["data", "compare", str(a), str(a), "--key", "nope"])

    assert result.exit_code == 2


def test_total_of_a_column_with_no_values_is_unknown_not_zero(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", "id,amount\n1,\n")
    b = _csv(tmp_path / "b.csv", "id,amount\n1,0\n")

    _, out = _compare(str(a), str(b), "--key", "id", "--total", "amount")

    assert out["data"]["totals"]["amount"] == {"a": None, "b": "0", "equal": False}


def test_a_trailing_newline_keeps_a_number_or_guid_as_text(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"id": "1", "qty": "12\n", "ref": _G1 + "\n"}])
    b = _jsonl(tmp_path / "b.jsonl", [{"id": "1", "qty": 12, "ref": _G1}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"qty": 1, "ref": 1}


def test_csv_boolean_text_equals_a_json_boolean_and_other_case_stays_text(
    tmp_path: Path,
) -> None:
    a = _csv(tmp_path / "a.csv", "id,active,flag\n1,True,TRUE\n")
    b = _jsonl(tmp_path / "b.jsonl", [{"id": 1, "active": True, "flag": "true"}])

    _, out = _compare(str(a), str(b), "--key", "id")

    assert out["data"]["field_mismatches"] == {"flag": 1}


def test_a_non_utf8_file_is_an_input_error_not_a_traceback(tmp_path: Path) -> None:
    a = tmp_path / "legacy.csv"
    a.write_bytes("id,name\n1,Caf\xe9\n".encode("cp1252"))

    code, out = _compare(str(a), str(a), "--key", "id")

    assert code == 1
    assert "not UTF-8" in out["error"]


def test_a_blank_key_is_ambiguous_and_never_paired(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.csv", "id,part,name\n,1,x\n2,,y\n")
    b = _csv(tmp_path / "b.csv", "id,part,name\n,1,z\n2,,w\n")

    _, out = _compare(str(a), str(b), "--key", "id,part")

    assert out["data"]["matched"] == 0
    assert out["data"]["mismatched"] == 0
    assert out["data"]["ambiguous"] == 2


def test_an_unsupported_suffix_names_the_file_once(tmp_path: Path) -> None:
    a = _csv(tmp_path / "a.txt", "id\n1\n")

    _, out = _compare(str(a), str(a), "--key", "id")

    assert out["error"].count("a.txt") == 1
