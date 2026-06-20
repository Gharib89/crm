"""Unit tests for crm.core.solution.retrieve_missing_components (#436).

RetrieveMissingComponents is a Web API *function* whose ``CustomizationFile``
parameter is the exported solution .zip bytes (Edm.Binary), passed as a
``binary'<base64>'`` parameter-alias literal — verified live against Dataverse.
All HTTP is mocked via requests_mock; no live D365 server.
"""
# pyright: basic
from __future__ import annotations

import base64
import json

import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import solution as sol_mod


def _write_solution(tmp_path, data: bytes = b"PK\x03\x04 fake solution zip"):
    path = tmp_path / "sol.zip"
    path.write_bytes(data)
    return path, data


def _mock_missing(m, backend, *, components):
    m.get(backend.url_for("RetrieveMissingComponents(CustomizationFile=@p1)"),
          json={"MissingComponents": components})


class TestRetrieveMissingComponents:
    def test_passes_file_as_binary_literal(self, backend, tmp_path):
        path, data = _write_solution(tmp_path)
        with requests_mock.Mocker() as m:
            _mock_missing(m, backend, components=[])
            out = sol_mod.retrieve_missing_components(backend, path)
        assert out["count"] == 0
        assert out["missing_components"] == []
        # The exported file bytes are base64'd inside a binary'...' literal.
        b64 = base64.b64encode(data).decode("ascii")
        sent = m.request_history[0].url
        assert "RetrieveMissingComponents(CustomizationFile=@p1)" in sent
        assert b64 in sent or b64.replace("+", "%2B").replace("/", "%2F").replace("=", "%3D") in sent

    def test_returns_missing_list(self, backend, tmp_path):
        path, _ = _write_solution(tmp_path)
        missing = [{"RequiredComponent": {"type": 1, "schemaName": "new_widget"}}]
        with requests_mock.Mocker() as m:
            _mock_missing(m, backend, components=missing)
            out = sol_mod.retrieve_missing_components(backend, path)
        assert out["count"] == 1
        assert out["missing_components"] == missing


class TestMissingComponentsCommand:
    def _stub_backend(self, monkeypatch, backend):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)

    def test_json_output(self, monkeypatch, backend, tmp_path):
        path, _ = _write_solution(tmp_path)
        self._stub_backend(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _mock_missing(m, backend, components=[{"RequiredComponent": {"type": 1}}])
            res = CliRunner().invoke(
                cli, ["--json", "solution", "missing-components", str(path)])
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)
        assert data["ok"] is True
        assert data["meta"]["count"] == 1
        assert len(data["data"]) == 1

    def test_missing_file_errors(self, monkeypatch, backend, tmp_path):
        self._stub_backend(monkeypatch, backend)
        res = CliRunner().invoke(
            cli, ["--json", "solution", "missing-components", str(tmp_path / "nope.zip")])
        assert res.exit_code != 0
