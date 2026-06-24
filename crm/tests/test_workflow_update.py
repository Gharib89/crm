"""Unit tests for crm.core.workflow.update_workflow and the `workflow update` CLI.

Exercised through the requests_mock + backend fixture seam (prior art:
test_workflow_clone.py / TestGetWorkflow). Asserts external behavior only — the
request the backend received, the emit envelope, error category/code — never
internal call order or private structure.
"""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock
from click.testing import CliRunner

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error

_WF_ID = "11111111-1111-1111-1111-111111111111"
_PARENT_ID = "22222222-2222-2222-2222-222222222222"
_CHILD_ID = "33333333-3333-3333-3333-333333333333"

# OData error code the server returns when an edit hits a published (activated)
# workflow definition; editing requires deactivate -> edit -> reactivate.
_LOCK = {"error": {"code": "0x80045002",
                   "message": "Cannot update a published workflow definition."}}


def _patches(m):
    return [r for r in m.request_history if r.method == "PATCH"]


def _dry_backend() -> D365Backend:
    profile = ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=True)


class TestUpdateWorkflow:
    def test_edits_draft_definition_in_place(self, backend):
        """A draft (statecode=0) is patched directly — no deactivate cycle."""
        from crm.core import workflow
        url = backend.url_for(f"workflows({_WF_ID})")
        with requests_mock.Mocker() as m:
            m.get(url, json={"name": "Old", "statecode": 0})
            m.patch(url, status_code=204)
            out = workflow.update_workflow(backend, _WF_ID, name="New name")
        assert len(_patches(m)) == 1
        assert _patches(m)[0].json() == {"name": "New name"}
        assert out["deactivated"] is False
        assert out["updated"] == {"name": "New name"}
        assert out["resolved_from_activation_id"] is None

    @pytest.mark.parametrize("kwarg,field,value", [
        ("name", "name", "Renamed"),
        ("scope", "scope", 4),
        ("on_demand", "ondemand", True),
        ("trigger_on_create", "triggeroncreate", True),
        ("trigger_on_delete", "triggerondelete", False),
        ("trigger_on_update_attributes", "triggeronupdateattributelist", "statecode,statuscode"),
        ("trigger_on_update_attributes", "triggeronupdateattributelist", ""),
    ])
    def test_each_metadata_field_maps_to_logical_name(self, backend, kwarg, field, value):
        """Every editable field reaches the PATCH body under its D365 logical
        name; False / "" are sent (not skipped — only None means leave-alone)."""
        from crm.core import workflow
        url = backend.url_for(f"workflows({_WF_ID})")
        with requests_mock.Mocker() as m:
            m.get(url, json={"name": "Old", "statecode": 0})
            m.patch(url, status_code=204)
            workflow.update_workflow(backend, _WF_ID, **{kwarg: value})
        assert _patches(m)[0].json() == {field: value}

    def test_no_fields_raises_before_any_write(self, backend):
        from crm.core import workflow
        url = backend.url_for(f"workflows({_WF_ID})")
        with requests_mock.Mocker() as m:
            m.get(url, json={"name": "Old", "statecode": 0})
            m.patch(url, status_code=204)
            with pytest.raises(D365Error, match="(?i)no .*field"):
                workflow.update_workflow(backend, _WF_ID)
        assert not _patches(m)

    def test_activated_definition_drives_deactivate_edit_reactivate(self, backend):
        """Published definition: the direct PATCH 0x80045002 triggers the
        deactivate -> edit -> reactivate cycle, in that order."""
        from crm.core import workflow
        url = backend.url_for(f"workflows({_WF_ID})")
        with requests_mock.Mocker() as m:
            m.get(url, json={"name": "Live WF", "statecode": 1})
            m.patch(url, [
                {"status_code": 400, "json": _LOCK},  # direct edit rejected
                {"status_code": 204},                 # deactivate
                {"status_code": 204},                 # metadata edit
                {"status_code": 204},                 # reactivate
            ])
            out = workflow.update_workflow(backend, _WF_ID, name="Renamed")
        bodies = [p.json() for p in _patches(m)]
        assert bodies == [
            {"name": "Renamed"},
            {"statecode": 0, "statuscode": 1},
            {"name": "Renamed"},
            {"statecode": 1, "statuscode": 2},
        ]
        assert out["deactivated"] is True

    def test_type2_activation_id_resolves_to_parent_before_edit(self, backend):
        from crm.core import workflow
        child_url = backend.url_for(f"workflows({_CHILD_ID})")
        parent_url = backend.url_for(f"workflows({_PARENT_ID})")
        with requests_mock.Mocker() as m:
            m.get(child_url, json={"_parentworkflowid_value": _PARENT_ID})
            m.get(parent_url, json={"name": "Parent def", "statecode": 0})
            m.patch(parent_url, status_code=204)
            out = workflow.update_workflow(backend, _CHILD_ID, name="X")
        assert _patches(m), "edit must target the parent definition"
        assert _PARENT_ID in _patches(m)[0].url
        assert out["workflow_id"] == _PARENT_ID
        assert out["resolved_from_activation_id"] == _CHILD_ID

    def test_dry_run_writes_nothing_but_runs_live_get(self):
        from crm.core import workflow
        backend = _dry_backend()
        url = backend.url_for(f"workflows({_WF_ID})")
        with requests_mock.Mocker() as m:
            m.get(url, json={"name": "Old", "statecode": 1})
            out = workflow.update_workflow(backend, _WF_ID, name="New", on_demand=True)
        assert not _patches(m), "dry-run must not PATCH"
        assert m.call_count >= 1, "the existence GET must run live"
        assert out["_dry_run"] is True
        assert out["would_update"] == {"name": "New", "ondemand": True}
        assert out["workflow_id"] == _WF_ID

    def test_non_lock_server_error_preserved_verbatim(self, backend):
        """A non-0x80045002 failure propagates with status/code/body intact and
        does not trigger a deactivate cycle."""
        from crm.core import workflow
        url = backend.url_for(f"workflows({_WF_ID})")
        err = {"error": {"code": "0x80040203", "message": "Bad value."}}
        with requests_mock.Mocker() as m:
            m.get(url, json={"name": "Old", "statecode": 0})
            m.patch(url, status_code=400, json=err)
            with pytest.raises(D365Error) as ei:
                workflow.update_workflow(backend, _WF_ID, scope=99)
        assert ei.value.code == "0x80040203"
        assert ei.value.status == 400
        assert ei.value.response_body == err
        assert len(_patches(m)) == 1, "no deactivate cycle for a non-lock error"

    def test_failed_reactivation_reported_truthfully(self, backend):
        """Record updated but reactivation failed -> raise (never a false
        success), preserving the reactivation server error."""
        from crm.core import workflow
        url = backend.url_for(f"workflows({_WF_ID})")
        react_err = {"error": {"code": "0x80048888", "message": "compile boom"}}
        with requests_mock.Mocker() as m:
            m.get(url, json={"name": "Live WF", "statecode": 1})
            m.patch(url, [
                {"status_code": 400, "json": _LOCK},   # direct edit rejected
                {"status_code": 204},                  # deactivate
                {"status_code": 204},                  # metadata edit
                {"status_code": 400, "json": react_err},  # reactivate FAILS
            ])
            with pytest.raises(D365Error) as ei:
                workflow.update_workflow(backend, _WF_ID, name="Renamed")
        assert "reactivation failed" in str(ei.value).lower()
        assert ei.value.code == "0x80048888"
        assert ei.value.status == 400
        assert ei.value.response_body == react_err


class TestUpdateCommand:
    def _seed_profile(self, monkeypatch, tmp_path):
        from crm.core import session as session_mod
        monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
        monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
        session_mod.save_profile(ConnectionProfile(
            name="t", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice"))
        session_mod.save_profile_secret_plaintext("t", "pw")

    def test_command_forwards_fields_and_maps_scope_name(self, monkeypatch, tmp_path):
        from crm.commands import workflow as wf_cmd
        from crm.cli import cli
        self._seed_profile(monkeypatch, tmp_path)
        called = {}

        def fake_update(backend, workflow_id, **kw):
            called.update(dict(workflow_id=workflow_id, **kw))
            return {"workflow_id": workflow_id, "updated": kw, "deactivated": False,
                    "resolved_from_activation_id": None}

        monkeypatch.setattr(wf_cmd.workflow_mod, "update_workflow", fake_update)
        result = CliRunner().invoke(cli, [
            "--profile", "t", "workflow", "update", _WF_ID,
            "--name", "New name", "--scope", "organization",
            "--on-create", "--no-on-demand",
            "--on-update-attributes", "statecode",
        ])
        assert result.exit_code == 0, result.output
        assert called["workflow_id"] == _WF_ID
        assert called["name"] == "New name"
        assert called["scope"] == 4
        assert called["trigger_on_create"] is True
        assert called["on_demand"] is False
        assert called["trigger_on_update_attributes"] == "statecode"
        # untouched flags stay None (leave-alone)
        assert called["trigger_on_delete"] is None

    def test_command_requires_at_least_one_field(self, monkeypatch, tmp_path):
        from crm.cli import cli
        self._seed_profile(monkeypatch, tmp_path)
        result = CliRunner().invoke(cli, ["--profile", "t", "workflow", "update", _WF_ID])
        assert result.exit_code == 2, result.output
        assert "at least one" in result.output.lower()

    def test_command_scope_accepts_integer(self, monkeypatch, tmp_path):
        from crm.commands import workflow as wf_cmd
        from crm.cli import cli
        self._seed_profile(monkeypatch, tmp_path)
        called = {}
        monkeypatch.setattr(wf_cmd.workflow_mod, "update_workflow",
                            lambda backend, workflow_id, **kw: called.update(kw)
                            or {"workflow_id": workflow_id})
        result = CliRunner().invoke(cli, [
            "--profile", "t", "workflow", "update", _WF_ID, "--scope", "2"])
        assert result.exit_code == 0, result.output
        assert called["scope"] == 2

    def test_command_rejects_out_of_range_scope(self, monkeypatch, tmp_path):
        """--scope help says 1–4; an out-of-range integer is rejected at parse
        time (exit 2), not deferred to a server error."""
        from crm.cli import cli
        self._seed_profile(monkeypatch, tmp_path)
        result = CliRunner().invoke(cli, [
            "--profile", "t", "workflow", "update", _WF_ID, "--scope", "99"])
        assert result.exit_code == 2, result.output
        assert "scope must be 1" in result.output.lower() or "out of range" in result.output.lower()
