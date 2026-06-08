"""Unit tests for crm.core.workflow export/import."""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock

from crm.core import workflow
from crm.utils.d365_backend import ConnectionProfile, D365Backend

_WF_ID = "8f9e7a6b-5c4d-3e2f-1a0b-9c8d7e6f5a4b"
_XAML = '<Activity x:Class="XrmWorkflowabc" />'


@pytest.fixture
def backend():
    profile = ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


class TestExportImport:
    def test_export_writes_file(self, backend, tmp_path):
        out_file = tmp_path / "wf.json"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_WF_ID})"), json={
                "workflowid": _WF_ID, "name": "Update request", "category": 0,
                "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
            })
            result = workflow.export_workflow(backend, _WF_ID, out_path=str(out_file))
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        assert saved["xaml"] == _XAML
        assert saved["primaryentity"] == "cwx_ticket"
        assert result["out_path"] == str(out_file)

    def test_import_upserts_from_file(self, backend, tmp_path):
        src = tmp_path / "wf.json"
        src.write_text(json.dumps({
            "workflowid": _WF_ID, "name": "Update request", "category": 0,
            "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
            "mode": 0, "scope": 4,
        }), encoding="utf-8")
        with requests_mock.Mocker() as m:
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.import_workflow(backend, file_path=str(src), activate=False)
        patches = [r for r in m.request_history if r.method == "PATCH"]
        assert patches[0].json()["xaml"] == _XAML
        assert out["workflow_id"] == _WF_ID
        assert out["activated"] is False
