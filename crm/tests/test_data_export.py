# pyright: basic
"""`data export` output that imports back (#1283): the `jsonl` format and the
opt-in `include_lookups`, with the default `json` and `csv` outputs unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests_mock

from crm.core import export as export_mod
from crm.utils.d365_backend import D365Backend

_ANNOT = "_ownerid_value@Microsoft.Dynamics.CRM.lookuplogicalname"
_GUID = "11111111-1111-1111-1111-111111111111"
_OWNER = "22222222-2222-2222-2222-222222222222"
_ROW = {"@odata.etag": 'W/"1"', "accountid": _GUID, "name": "Acme", "_ownerid_value": _OWNER}
_ANNOTATED = {**_ROW, _ANNOT: "systemuser"}
_ANNOT_PREFER = 'odata.include-annotations="Microsoft.Dynamics.CRM.lookuplogicalname"'


def _export(backend: D365Backend, out: Path, rows: list[dict], **kw):
    with requests_mock.Mocker() as m:
        m.get(backend.url_for("accounts"), json={"value": rows})
        info = export_mod.export_records(backend, "accounts", out, **kw)
        prefers = [r.headers.get("Prefer") for r in m.request_history]
    return info, prefers


class TestDefaultsUnchanged:
    def test_json_bytes_and_headers(self, backend: D365Backend, tmp_path: Path) -> None:
        out = tmp_path / "a.json"
        _, prefers = _export(backend, out, [_ROW])
        assert out.read_text(encoding="utf-8") == json.dumps([_ROW], indent=2, default=str)
        assert prefers == ["odata.maxpagesize=500"]

    def test_csv_bytes_and_headers(self, backend: D365Backend, tmp_path: Path) -> None:
        out = tmp_path / "a.csv"
        _, prefers = _export(backend, out, [_ROW])
        assert out.read_bytes() == f"accountid,name\r\n{_GUID},Acme\r\n".encode()
        assert prefers == ["odata.maxpagesize=500"]


class TestJsonl:
    def test_one_object_per_line_with_annotation_requested(
        self, backend: D365Backend, tmp_path: Path
    ) -> None:
        out = tmp_path / "a.out"
        info, prefers = _export(backend, out, [_ANNOTATED, _ANNOTATED], fmt="jsonl")
        lines = out.read_text(encoding="utf-8").splitlines()
        assert [json.loads(line) for line in lines] == [_ANNOTATED, _ANNOTATED]
        assert info["format"] == "jsonl"
        assert prefers == [f"{_ANNOT_PREFER},odata.maxpagesize=500"]

    def test_suffix_infers_jsonl(self, backend: D365Backend, tmp_path: Path) -> None:
        info, _ = _export(backend, tmp_path / "a.jsonl", [_ANNOTATED])
        assert info["format"] == "jsonl"

    def test_next_page_keeps_the_annotation_request(
        self, backend: D365Backend, tmp_path: Path
    ) -> None:
        next_link = backend.url_for("accounts") + "?$skiptoken=abc"
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("accounts"),
                [
                    {"json": {"value": [_ANNOTATED], "@odata.nextLink": next_link}},
                    {"json": {"value": [_ANNOTATED]}},
                ],
            )
            export_mod.export_records(backend, "accounts", tmp_path / "a.jsonl")
            prefers = [r.headers.get("Prefer") for r in m.request_history]
        assert len(prefers) == 2
        assert all(_ANNOT_PREFER in (p or "") for p in prefers), prefers


class TestIncludeLookups:
    def test_json_requests_the_annotation(self, backend: D365Backend, tmp_path: Path) -> None:
        out = tmp_path / "a.json"
        _, prefers = _export(backend, out, [_ANNOTATED], include_lookups=True)
        assert json.loads(out.read_text(encoding="utf-8")) == [_ANNOTATED]
        assert prefers == [f"{_ANNOT_PREFER},odata.maxpagesize=500"]

    def test_csv_adds_value_columns(self, backend: D365Backend, tmp_path: Path) -> None:
        out = tmp_path / "a.csv"
        _export(backend, out, [_ROW], include_lookups=True)
        header = out.read_text(encoding="utf-8").splitlines()[0]
        assert header == "accountid,name,_ownerid_value"


def test_cli_passes_format_and_include_lookups(monkeypatch, tmp_path: Path) -> None:
    from click.testing import CliRunner

    from crm.cli import CLIContext, cli

    seen: dict = {}

    def _fake(backend, entity_set, output, **kw):
        seen.update(kw)
        return {"output": str(output), "format": kw["fmt"], "count": 0, "entity_set": entity_set}

    monkeypatch.setattr(CLIContext, "backend", lambda self: object())
    monkeypatch.setattr(export_mod, "export_records", _fake)
    out = tmp_path / "a.jsonl"
    args = ["data", "export", "accounts", "-o", str(out), "--format", "jsonl", "--include-lookups"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert seen["fmt"] == "jsonl"
    assert seen["include_lookups"] is True
