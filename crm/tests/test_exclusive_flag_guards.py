# pyright: basic
"""Exactly-one flag guards treat an empty value as given, not absent (#1412).

Each command below takes exactly one of two flags. Passing both exits 2
whatever their values, and one flag given alone as "" exits 2 too, before any
backend is built.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from crm.cli import cli

_GUID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def no_backend(monkeypatch):
    def _boom(self):
        raise AssertionError("backend built past the usage guard")

    monkeypatch.setattr("crm.cli.CLIContext.backend", _boom)


@pytest.fixture
def rdl(tmp_path):
    path = tmp_path / "r.rdl"
    path.write_text("<Report/>", encoding="utf-8")
    return str(path)


@pytest.fixture
def json_file(tmp_path):
    path = tmp_path / "d.json"
    path.write_text('{"name": "x"}', encoding="utf-8")
    return str(path)


@pytest.fixture
def fetch_file(tmp_path):
    path = tmp_path / "q.xml"
    path.write_text("<fetch><entity name='account'/></fetch>", encoding="utf-8")
    return str(path)


_CASES = [
    (["solution", "create", "--name", "S"], [("--publisher", ""), ("--publisher-id", _GUID)]),
    (["solution", "create", "--name", "S"], [("--publisher", "p"), ("--publisher-id", "")]),
    (["solution", "create", "--name", "S"], [("--publisher", "")]),
    (["solution", "create", "--name", "S"], [("--publisher-id", "")]),
    (["fieldsec", "assign", "P"], [("--user-id", ""), ("--team", "T")]),
    (["fieldsec", "assign", "P"], [("--user-id", "U"), ("--team", "")]),
    (["fieldsec", "assign", "P"], [("--user", ""), ("--team", "T")]),
    (["fieldsec", "assign", "P"], [("--user-id", "")]),
    (["fieldsec", "assign", "P"], [("--team", "")]),
    (["fieldsec", "unassign", "P", "--yes"], [("--user-id", ""), ("--team", "T")]),
    (["fieldsec", "unassign", "P", "--yes"], [("--user-id", "U"), ("--team", "")]),
    (["fieldsec", "unassign", "P", "--yes"], [("--user-id", "")]),
    (["fieldsec", "unassign", "P", "--yes"], [("--team", "")]),
    (["data", "delete", "accounts", "--yes"], [("--fetchxml", ""), ("--fetchxml-file", "@FETCH")]),
    (["data", "delete", "accounts", "--yes"], [("--fetchxml", "")]),
    (
        ["report", "create", "--name", "R", "--solution", "S"],
        [("--body-file", "@RDL"), ("--url", "")],
    ),
    (["report", "create", "--name", "R", "--solution", "S"], [("--url", "")]),
    (["security", "assign-role", "R"], [("--to-user", ""), ("--to-team", "T")]),
    (["security", "assign-role", "R"], [("--to-user", "")]),
    (["security", "unassign-role", "R", "--yes"], [("--from-user", "U"), ("--from-team", "")]),
    (["security", "unassign-role", "R", "--yes"], [("--from-team", "")]),
    (["entity", "create", "accounts"], [("--data", ""), ("--data-file", "@JSON")]),
]


@pytest.mark.parametrize(
    ("prefix", "flags"),
    _CASES,
    ids=[" ".join([*p[:2], *(f"{k}={v!r}" for k, v in f)]) for p, f in _CASES],
)
def test_empty_value_never_satisfies_exactly_one(
    no_backend, rdl, fetch_file, json_file, prefix, flags
):
    files = {"@RDL": rdl, "@FETCH": fetch_file, "@JSON": json_file}
    args = [arg for flag, value in flags for arg in (flag, files.get(value, value))]
    result = CliRunner().invoke(cli, ["--json", *prefix, *args])
    assert result.exit_code == 2, result.output


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("accountid,name\n,x\n", encoding="utf-8")
    return str(path)


_SOL = ["--solution", "S"]
_SUBAREA = ["sitemap", "add-subarea", "SM", "--area", "A", "--group", "G", "--id", "S1", *_SOL]
_STEP = ["plugin", "register-step", "--message", "Create", *_SOL]
_OTHER_SHAPES = [
    ["ribbon", "export", "--application", ""],
    ["entity", "upsert", "accounts", "", "--key", "name", "--data", '{"name": "x"}'],
    ["data", "import", "accounts", "@CSV", "--mode", "upsert", "--id-column", "", "--key", "name"],
    ["data", "import", "accounts", "@CSV", "--key", ""],
    ["chart", "create", "account", "--name", "C", *_SOL, "--web-resource", ""],
    ["query", "fetchxml", "accounts", "--xml", "", "--file", "@FETCH"],
    ["dup", "bulk-detect", "account", "--fetchxml", "", "--fetchxml-file", "@FETCH"],
    [
        "sla", "add-kpi", "--sla", _GUID, "--kpi", "k", *_SOL, "--success-criteria", "x",
        "--applicable-when", "", "--applicable-when-file", "@FETCH",
    ],
    [*_STEP, "--plugin-type", ""],
    [*_STEP, "--plugin-type", "", "--service-endpoint", "S"],
    [*_SUBAREA, "--url", "", "--entity", "account"],
    [*_SUBAREA, "--url", "  ", "--entity", "account"],
    ["sitemap", "move-node", "SM", "--id", "N", *_SOL, "--before", "", "--index", "0"],
    ["dashboard", "remove-component", _GUID, *_SOL, "--url", "", "--chart", "C"],
    ["dashboard", "remove-component", _GUID, *_SOL, "--cell-id", "", "--index", "0"],
]  # fmt: skip


@pytest.mark.parametrize("argv", _OTHER_SHAPES, ids=[" ".join(map(repr, a)) for a in _OTHER_SHAPES])
def test_empty_value_counts_as_given_in_other_guard_shapes(no_backend, fetch_file, csv_file, argv):
    """Split if/if, at-most-one and strip-then-count guards follow #1412 too (#1415)."""
    files = {"@FETCH": fetch_file, "@CSV": csv_file}
    result = CliRunner().invoke(cli, ["--json", *(files.get(a, a) for a in argv)])
    assert result.exit_code == 2, result.output
