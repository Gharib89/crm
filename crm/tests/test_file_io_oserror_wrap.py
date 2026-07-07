# pyright: basic
"""Disk-IO failures render as the house clean error, never a traceback (#699).

Sweeps the file read/write paths that previously did unguarded disk IO:

- ``crm/core/export.py`` — data export mkdir + JSON/CSV write
- ``crm/core/solution_transfer.py`` — solution export write, import read
- ``crm form export`` — formxml output write (command layer)
- ``crm query fetchxml --file`` — FetchXML file input read (command layer)

Each site is exercised with a monkeypatched filesystem failure (an ``OSError``
raised from the underlying ``pathlib`` operation — the portable stand-in for a
chmod-0 / missing-dir failure, and Windows-safe). Core sites must raise
``D365Error`` naming the path; command-layer sites must surface the clean JSON
envelope with the right exit code, not an unhandled traceback.

The shared body-file read helpers (``crm/commands/_helpers/parsing.py``
``_load_payload`` / ``_read_file``) are intentionally NOT swept here: they
already wrap ``OSError`` → ``click.UsageError`` (verified as already-satisfied
for #699 — see the PR notes), which is the correct exit-2 treatment for a
command-layer input file.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
import requests_mock as rm_module
from click.testing import CliRunner

from crm.cli import cli
from crm.core import export as export_mod
from crm.core import solution_transfer as st_mod
from crm.utils.d365_backend import D365Error


def _raise_oserror(*_args, **_kwargs):
    raise OSError("simulated filesystem failure")


# --------------------------------------------------------------------------- #
# Core: crm/core/export.py  (export_records)
# --------------------------------------------------------------------------- #
class TestExportRecordsIO:
    @pytest.fixture(autouse=True)
    def _stub_iter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Isolate the write path from the backend/query: yield a fixed record.
        monkeypatch.setattr(
            export_mod, "_iter_records", lambda *a, **k: [{"id": "1", "name": "x"}]
        )

    def test_json_write_failure_raises_d365error_naming_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "export.json"
        monkeypatch.setattr(Path, "write_text", _raise_oserror)
        with pytest.raises(D365Error) as exc:
            export_mod.export_records(None, "accounts", str(out), fmt="json")  # type: ignore[arg-type]
        assert "export.json" in str(exc.value)

    def test_csv_write_failure_raises_d365error_naming_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "export.csv"
        monkeypatch.setattr(Path, "open", _raise_oserror)
        with pytest.raises(D365Error) as exc:
            export_mod.export_records(None, "accounts", str(out), fmt="csv")  # type: ignore[arg-type]
        assert "export.csv" in str(exc.value)

    def test_mkdir_failure_raises_d365error_naming_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "nested" / "export.json"
        monkeypatch.setattr(Path, "mkdir", _raise_oserror)
        with pytest.raises(D365Error) as exc:
            export_mod.export_records(None, "accounts", str(out), fmt="json")  # type: ignore[arg-type]
        assert "export.json" in str(exc.value)


# --------------------------------------------------------------------------- #
# Core: crm/core/solution_transfer.py
# --------------------------------------------------------------------------- #
class TestSolutionTransferIO:
    def test_export_write_failure_raises_d365error_naming_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "solution.zip"
        encoded = base64.b64encode(b"zip-bytes").decode("ascii")
        monkeypatch.setattr(Path, "write_bytes", _raise_oserror)
        with pytest.raises(D365Error) as exc:
            st_mod._write_export_file(str(out), encoded)
        assert "solution.zip" in str(exc.value)

    def test_export_mkdir_failure_raises_d365error_naming_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "nested" / "solution.zip"
        encoded = base64.b64encode(b"zip-bytes").decode("ascii")
        monkeypatch.setattr(Path, "mkdir", _raise_oserror)
        with pytest.raises(D365Error) as exc:
            st_mod._write_export_file(str(out), encoded)
        assert "solution.zip" in str(exc.value)

    def test_import_read_failure_raises_d365error_naming_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A real file so the is_file() guard passes; the read itself then fails.
        zip_path = tmp_path / "import.zip"
        zip_path.write_bytes(b"not-really-a-zip")
        monkeypatch.setattr(Path, "read_bytes", _raise_oserror)
        with pytest.raises(D365Error) as exc:
            st_mod.import_solution(object(), str(zip_path))  # type: ignore[arg-type]
        assert "import.zip" in str(exc.value)


# --------------------------------------------------------------------------- #
# Command layer: crm form export  (formxml output write)
# --------------------------------------------------------------------------- #
_FORM = {
    "formid": "aaaaaaaa-0000-0000-0000-000000000001",
    "name": "Information",
    "objecttypecode": "new_project",
    "type": 2,
    "formxml": "<form/>",
    "isdefault": True,
}


def test_form_export_write_failure_clean_envelope(
    backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
    out_file = tmp_path / "form.xml"
    real_write_text = Path.write_text

    def _selective(self: Path, *a, **k):
        if str(self) == str(out_file):
            raise OSError("simulated filesystem failure")
        return real_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _selective)
    with rm_module.Mocker() as m:
        m.get(backend.url_for("systemforms"), json={"value": [_FORM]})
        result = CliRunner().invoke(cli, [
            "--json", "form", "export", "new_project", "Information",
            "--output", str(out_file),
        ])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "form.xml" in env["error"]


# --------------------------------------------------------------------------- #
# Command layer: crm query fetchxml --file  (FetchXML input read)
# --------------------------------------------------------------------------- #
def test_query_fetchxml_file_read_failure_clean_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xml_file = tmp_path / "query.xml"
    xml_file.write_text("<fetch/>", encoding="utf-8")
    real_read_text = Path.read_text

    def _selective(self: Path, *a, **k):
        if str(self) == str(xml_file):
            raise OSError("simulated filesystem failure")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _selective)
    # Positional ENTITY_SET avoids any resolver round-trip; the read fails first
    # regardless, so no backend is needed.
    result = CliRunner().invoke(cli, [
        "--json", "query", "fetchxml", "accounts", "--file", str(xml_file),
    ])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert "query.xml" in env["error"]
