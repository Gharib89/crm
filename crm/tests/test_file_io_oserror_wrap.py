# pyright: basic
"""Disk-IO failures render as the house clean error, never a traceback (#699).

Sweeps the file read/write paths that previously did unguarded disk IO:

- ``crm/core/export.py`` — data export mkdir + JSON/CSV write
- ``crm/core/solution_transfer.py`` — solution export write, import read
- ``crm form export`` — formxml output write (command layer)
- ``crm query fetchxml --file`` — FetchXML file input read (command layer)
- ``crm solution publish --xml-file`` — Publish XML file input read (command layer)
- ``crm app set-sitemap --xml-file`` — SiteMap XML file input read (command layer)

Each site is exercised with a monkeypatched filesystem failure (an ``OSError``
raised from the underlying ``pathlib`` operation — the portable stand-in for a
chmod-0 / missing-dir failure, and Windows-safe). Core sites must raise
``D365Error`` naming the path; command-layer sites must surface the clean JSON
envelope with the right exit code, not an unhandled traceback.

The shared body-file read helpers (``crm/commands/_helpers/parsing.py``
``_load_payload`` / ``_read_file``) are intentionally NOT swept here: they
already wrap ``OSError`` → ``click.UsageError`` (verified as already-satisfied
for #699 — the correct exit-2 treatment for a command-layer input file), so
churning them would only add noise.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Callable

import pytest
import requests_mock as rm_module
from click.testing import CliRunner

from crm.cli import cli
from crm.core import export as export_mod
from crm.core import solution_transfer as st_mod
from crm.utils.d365_backend import D365Error

# The command-layer tests drive the CLI via CliRunner; isolate CRM_HOME and
# scrub legacy credential env vars so a developer's real profile/env can't sway
# the run (per crm/tests/conftest.py's isolated_home guidance).
pytestmark = pytest.mark.usefixtures("isolated_home")


def _raise_oserror(*_args, **_kwargs):
    raise OSError("simulated filesystem failure")


# --------------------------------------------------------------------------- #
# Core sites — each must raise D365Error naming the path AND the operation
# (exit 1 at the CLI).
#
# A scenario builder receives tmp_path and returns
#   (pathlib_method_to_break, expected_path, call_the_site, expected_operation)
# so the parametrized test can break exactly one filesystem op per site and
# assert the message names both the path and whether it was a read or write.
# --------------------------------------------------------------------------- #
def _export_json_write(tmp_path: Path):
    out = tmp_path / "export.json"
    return (
        "write_text",
        out,
        lambda: export_mod.export_records(None, "accounts", str(out), fmt="json"),
        "cannot write",
    )  # type: ignore[arg-type]


def _export_csv_write(tmp_path: Path):
    out = tmp_path / "export.csv"
    return (
        "open",
        out,
        lambda: export_mod.export_records(None, "accounts", str(out), fmt="csv"),
        "cannot write",
    )  # type: ignore[arg-type]


def _export_mkdir(tmp_path: Path):
    out = tmp_path / "nested" / "export.json"
    return (
        "mkdir",
        out,
        lambda: export_mod.export_records(None, "accounts", str(out), fmt="json"),
        "cannot write",
    )  # type: ignore[arg-type]


def _solution_export_write(tmp_path: Path):
    out = tmp_path / "solution.zip"
    encoded = base64.b64encode(b"zip-bytes").decode("ascii")
    return "write_bytes", out, lambda: st_mod._write_export_file(str(out), encoded), "cannot write"


def _solution_export_mkdir(tmp_path: Path):
    out = tmp_path / "nested" / "solution.zip"
    encoded = base64.b64encode(b"zip-bytes").decode("ascii")
    return "mkdir", out, lambda: st_mod._write_export_file(str(out), encoded), "cannot write"


def _solution_import_read(tmp_path: Path):
    zip_path = tmp_path / "import.zip"
    zip_path.write_bytes(b"not-really-a-zip")  # is_file() guard must pass first
    return (
        "read_bytes",
        zip_path,
        lambda: st_mod.import_solution(object(), str(zip_path)),
        "cannot read",
    )  # type: ignore[arg-type]


_CORE_CASES = [
    pytest.param(_export_json_write, id="export-json-write"),
    pytest.param(_export_csv_write, id="export-csv-write"),
    pytest.param(_export_mkdir, id="export-mkdir"),
    pytest.param(_solution_export_write, id="solution-export-write"),
    pytest.param(_solution_export_mkdir, id="solution-export-mkdir"),
    pytest.param(_solution_import_read, id="solution-import-read"),
]


@pytest.mark.parametrize("scenario", _CORE_CASES)
def test_core_io_failure_raises_d365error_naming_path(
    scenario: Callable[[Path], tuple], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Isolate the export write paths from the backend/query: yield a fixed record.
    monkeypatch.setattr(export_mod, "_iter_records", lambda *a, **k: [{"id": "1", "name": "x"}])
    method, expected_path, call_site, expected_op = scenario(tmp_path)
    monkeypatch.setattr(Path, method, _raise_oserror)
    with pytest.raises(D365Error) as exc:
        call_site()
    message = str(exc.value)
    # Name the path AND the operation (read vs write), per the #699 contract.
    assert expected_path.name in message
    assert expected_op in message.lower()


# --------------------------------------------------------------------------- #
# Command layer: input-file reads — clean envelope (exit 1), not a traceback.
# --------------------------------------------------------------------------- #
_CMD_READ_CASES = [
    pytest.param(
        ["query", "fetchxml", "accounts", "--file"], "query.xml", id="query-fetchxml-file"
    ),
    pytest.param(
        ["solution", "publish", "--xml-file"], "publish.xml", id="solution-publish-xml-file"
    ),
    pytest.param(
        ["app", "set-sitemap", "MySite", "--xml-file"], "sitemap.xml", id="app-set-sitemap-xml-file"
    ),
]


@pytest.mark.parametrize("argv,filename", _CMD_READ_CASES)
def test_command_input_read_failure_clean_envelope(
    argv: list[str], filename: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # click.Path(exists=True) requires a real file; the read itself then fails.
    target = tmp_path / filename
    target.write_text("<x/>", encoding="utf-8")
    real_read_text = Path.read_text

    def _selective(self: Path, *a, **k):
        if str(self) == str(target):
            raise OSError("simulated filesystem failure")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _selective)
    # The read fails before any backend call, so no backend injection is needed.
    result = CliRunner().invoke(cli, ["--json", *argv, str(target)])
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    # Name the path AND the operation (both cases are input reads).
    assert filename in env["error"]
    assert "could not read" in env["error"].lower()


# --------------------------------------------------------------------------- #
# Command layer: crm form export — formxml output write (needs a backend read
# to reach the write), so it gets a dedicated test rather than the sweep above.
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
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "form",
                "export",
                "new_project",
                "Information",
                "--output",
                str(out_file),
            ],
        )
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    # Name the path AND the operation (write).
    assert "form.xml" in env["error"]
    assert "could not write" in env["error"].lower()
