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


_TRIGGER_FIELDS = {
    "triggeroncreate": True, "triggerondelete": False,
    "triggeronupdateattributelist": None,
    "asyncautodelete": True, "runas": 1,
    "syncworkflowlogonfailure": False, "istransacted": True,
}


class TestExportImport:
    def test_export_writes_file(self, backend, tmp_path):
        out_file = tmp_path / "wf.json"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"workflows({_WF_ID})"), json={
                "workflowid": _WF_ID, "name": "Update request", "category": 0,
                "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
                **_TRIGGER_FIELDS,
            })
            result = workflow.export_workflow(backend, _WF_ID, out_path=str(out_file))
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        assert saved["xaml"] == _XAML
        assert saved["primaryentity"] == "cwx_ticket"
        assert result["out_path"] == str(out_file)
        assert saved["triggeroncreate"] is True
        assert saved["asyncautodelete"] is True

    def test_import_upserts_from_file(self, backend, tmp_path):
        src = tmp_path / "wf.json"
        src.write_text(json.dumps({
            "workflowid": _WF_ID, "name": "Update request", "category": 0,
            "primaryentity": "cwx_ticket", "type": 1, "xaml": _XAML,
            "mode": 0, "scope": 4,
            **_TRIGGER_FIELDS,
        }), encoding="utf-8")
        with requests_mock.Mocker() as m:
            m.patch(requests_mock.ANY, status_code=204)
            out = workflow.import_workflow(backend, file_path=str(src), activate=False)
        patches = [r for r in m.request_history if r.method == "PATCH"]
        body = patches[0].json()
        assert body["xaml"] == _XAML
        assert body["triggeroncreate"] is True
        assert body["asyncautodelete"] is True
        assert body["type"] == 1
        assert out["workflow_id"] == _WF_ID
        assert out["activated"] is False


from click.testing import CliRunner


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    session_mod.save_profile(ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


class TestExportImportCommands:
    def test_export_command(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd
        captured = {}
        monkeypatch.setattr(wf_cmd.workflow_mod, "export_workflow",
                            lambda backend, wid, **kw: captured.update(id=wid, **kw) or {"out_path": kw.get("out_path")})
        from crm.cli import cli
        result = CliRunner().invoke(cli,
            ["--profile", "t", "workflow", "export", _WF_ID, "--out", str(tmp_path / "x.json")])
        assert result.exit_code == 0, result.output
        assert captured["id"] == _WF_ID

    def test_import_command(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd
        captured = {}
        monkeypatch.setattr(wf_cmd.workflow_mod, "import_workflow",
                            lambda backend, **kw: captured.update(**kw) or {"workflow_id": "x", "activated": False})
        f = tmp_path / "x.json"; f.write_text("{}", encoding="utf-8")
        from crm.cli import cli
        result = CliRunner().invoke(cli,
            ["--profile", "t", "workflow", "import", "--file", str(f)])
        assert result.exit_code == 0, result.output
        assert captured["file_path"] == str(f)
