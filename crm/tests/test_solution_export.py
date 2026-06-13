"""Unit tests for crm.core.solution.export_solution (sync fallback)."""
# pyright: basic

from __future__ import annotations

import base64

import pytest
import requests_mock

from crm.utils.d365_backend import D365Error


_ZIP_BYTES = b"PK\x03\x04 fake solution zip"


def test_async_unavailable_predicate():
    from crm.core import solution as sol
    yes = D365Error("ExportSolutionAsync is not enabled for this org", status=400)
    no = D365Error("Some unrelated server error", status=500)
    assert sol._async_export_unavailable(yes) is True
    assert sol._async_export_unavailable(no) is False


def test_export_falls_back_to_sync_when_async_disabled(backend, tmp_path):
    from crm.core import solution as sol
    out = tmp_path / "crmworx.zip"
    encoded = base64.b64encode(_ZIP_BYTES).decode("ascii")
    with requests_mock.Mocker() as m:
        m.post(
            backend.url_for("ExportSolutionAsync"),
            status_code=400,
            json={"error": {"code": "0x80040224",
                            "message": "ExportSolutionAsync is not enabled for this org"}},
        )
        m.post(
            backend.url_for("ExportSolution"),
            json={"ExportSolutionFile": encoded},
        )
        info = sol.export_solution(backend, "CRMWorx", out)
    assert info["action"] == "ExportSolution"
    assert info["bytes"] == len(_ZIP_BYTES)
    assert out.read_bytes() == _ZIP_BYTES


def test_export_async_error_other_than_unavailable_propagates(backend, tmp_path):
    from crm.core import solution as sol
    with requests_mock.Mocker() as m:
        m.post(
            backend.url_for("ExportSolutionAsync"),
            status_code=500,
            json={"error": {"code": "0x", "message": "boom"}},
        )
        with pytest.raises(D365Error, match="boom"):
            sol.export_solution(backend, "CRMWorx", tmp_path / "x.zip")
