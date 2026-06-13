"""Tests for `crm sla activate` (issue #168)."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core.sla import parse_error_map


class TestParseErrorMap:
    def test_parses_steps_and_errors(self):
        msg = (
            "This workflow has errors. ErrorMap Details: "
            "{ConditionBranchStep2: InvalidEntity, InvalidRelationship; "
            "SetPropertyStep5: InvalidEntity}"
        )
        assert parse_error_map(msg) == [
            {"step": "ConditionBranchStep2",
             "errors": ["InvalidEntity", "InvalidRelationship"]},
            {"step": "SetPropertyStep5", "errors": ["InvalidEntity"]},
        ]

    def test_returns_none_when_no_error_map(self):
        assert parse_error_map("This workflow has errors.") is None

    def test_returns_none_when_map_empty(self):
        assert parse_error_map("ErrorMap Details: {}") is None


import requests_mock

from crm.core.sla import activate_sla
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error

_SLA_ID = "625c0c25-e31b-f111-b119-005056010908"
_WF1 = "11111111-2222-3333-4444-555555555555"
_WF2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.1", verify_ssl=False,
    )


def _sla_row(statecode=0):
    return {"slaid": _SLA_ID, "name": "Gold SLA", "statecode": statecode}


def _items_payload():
    return {"value": [
        {"slaitemid": "0000000a-000a-000a-000a-00000000000a",
         "name": "First response", "_workflowid_value": _WF1},
        {"slaitemid": "0000000b-000b-000b-000b-00000000000b",
         "name": "Resolve by", "_workflowid_value": _WF2},
    ]}


def _wf_row(wid, statecode=0):
    return {"workflowid": wid, "name": f"SLA wf {wid[:4]}",
            "type": 1, "statecode": statecode}


def _requests(m, method):
    return [r for r in m.request_history if r.method == method]


def _mock_sla_reads(m, backend, sla_state=0, wf_states=(0, 0)):
    m.get(backend.url_for(f"slas({_SLA_ID})"), json=_sla_row(sla_state))
    m.get(backend.url_for("slaitems"), json=_items_payload())
    for wid, state in zip((_WF1, _WF2), wf_states):
        m.get(backend.url_for(f"workflows({wid})"), json=_wf_row(wid, state))


class TestActivateSla:
    def test_happy_path_activates_workflows_then_sla(self, backend):
        with requests_mock.Mocker() as m:
            _mock_sla_reads(m, backend)
            m.patch(backend.url_for(f"workflows({_WF1})"), status_code=204)
            m.patch(backend.url_for(f"workflows({_WF2})"), status_code=204)
            m.patch(backend.url_for(f"slas({_SLA_ID})"), status_code=204)
            result = activate_sla(backend, _SLA_ID)

        assert result["sla_activated"] is True
        assert result["sla_id"] == _SLA_ID
        assert [w["status"] for w in result["workflows"]] == ["activated", "activated"]
        patches = _requests(m, "PATCH")
        assert len(patches) == 3
        # SLA is patched last, only after every backing workflow succeeded
        assert f"slas({_SLA_ID})".lower() in patches[-1].url.lower()
        assert patches[-1].json() == {"statecode": 1, "statuscode": 2}

    def test_already_active_workflow_skipped(self, backend):
        """Re-running is safe: active backing workflows are not PATCHed and
        report as already_active."""
        with requests_mock.Mocker() as m:
            _mock_sla_reads(m, backend, wf_states=(1, 0))
            m.patch(backend.url_for(f"workflows({_WF2})"), status_code=204)
            m.patch(backend.url_for(f"slas({_SLA_ID})"), status_code=204)
            result = activate_sla(backend, _SLA_ID)

        assert result["sla_activated"] is True
        assert [w["status"] for w in result["workflows"]] == [
            "already_active", "activated"]
        patched_urls = [r.url.lower() for r in _requests(m, "PATCH")]
        assert not any(f"workflows({_WF1})" in u for u in patched_urls)

    def test_compile_failure_blocks_sla_and_reports_structured_errors(self, backend):
        """A backing workflow with compile errors: remaining workflows are
        still attempted, the SLA is never PATCHed, and the failure carries
        per-step structured errors plus the raw platform message."""
        compile_msg = ("This workflow has errors. ErrorMap Details: "
                       "{ConditionBranchStep2: InvalidEntity, InvalidRelationship}")
        with requests_mock.Mocker() as m:
            _mock_sla_reads(m, backend)
            m.patch(backend.url_for(f"workflows({_WF1})"), status_code=400,
                    json={"error": {"code": "0x80048455", "message": compile_msg}})
            m.patch(backend.url_for(f"workflows({_WF2})"), status_code=204)
            m.patch(backend.url_for(f"slas({_SLA_ID})"), status_code=204)
            result = activate_sla(backend, _SLA_ID)

        assert result["sla_activated"] is False
        assert result["ui_activation_required"] is True
        failed, ok = result["workflows"]
        assert failed["status"] == "failed"
        assert failed["errors"] == [{"step": "ConditionBranchStep2",
                                     "errors": ["InvalidEntity", "InvalidRelationship"]}]
        assert compile_msg in failed["error"]
        assert ok["status"] == "activated"
        patched_urls = [r.url.lower() for r in _requests(m, "PATCH")]
        assert not any(f"slas({_SLA_ID})" in u for u in patched_urls)

    def test_unparseable_failure_falls_back_to_raw_error(self, backend):
        with requests_mock.Mocker() as m:
            _mock_sla_reads(m, backend)
            m.patch(backend.url_for(f"workflows({_WF1})"), status_code=400,
                    json={"error": {"code": "0xdead", "message": "boom, no map"}})
            m.patch(backend.url_for(f"workflows({_WF2})"), status_code=204)
            result = activate_sla(backend, _SLA_ID)

        failed = result["workflows"][0]
        assert failed["status"] == "failed"
        assert "errors" not in failed
        assert "boom, no map" in failed["error"]

    def test_already_active_sla_not_repatched(self, backend):
        """Fully-active SLA: re-run touches nothing and reports as such."""
        with requests_mock.Mocker() as m:
            _mock_sla_reads(m, backend, sla_state=1, wf_states=(1, 1))
            result = activate_sla(backend, _SLA_ID)

        assert result["sla_activated"] is True
        assert result["sla_already_active"] is True
        assert _requests(m, "PATCH") == []

    def test_dry_run_returns_preview_and_never_patches(self, profile):
        """Dry-run resolves the plan with live GETs but performs no PATCH and
        returns a structured preview, never a bare success payload."""
        backend = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            _mock_sla_reads(m, backend, wf_states=(1, 0))
            result = activate_sla(backend, _SLA_ID)

        assert result["_dry_run"] is True
        assert result["would_activate"] == [
            {"workflow_id": _WF2, "name": f"SLA wf {_WF2[:4]}"}]
        assert result["already_active"] == [
            {"workflow_id": _WF1, "name": f"SLA wf {_WF1[:4]}"}]
        assert result["would_activate_sla"] is True
        assert "sla_activated" not in result
        assert _requests(m, "PATCH") == []
        assert backend.dry_run is True  # flag restored after live GETs

    def test_invalid_guid_fails_before_any_request(self, backend):
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="Invalid GUID"):
                activate_sla(backend, "not-a-guid")
        assert m.request_history == []

    def test_braced_uppercase_guid_is_normalized(self, backend):
        """A braced/uppercase GUID is canonicalized before hitting any URL or
        $filter, and the result reports the canonical form."""
        with requests_mock.Mocker() as m:
            _mock_sla_reads(m, backend, wf_states=(1, 1))
            m.patch(backend.url_for(f"slas({_SLA_ID})"), status_code=204)
            result = activate_sla(backend, "{" + _SLA_ID.upper() + "}")

        assert result["sla_id"] == _SLA_ID
        first_get = m.request_history[0]
        assert f"slas({_SLA_ID})".lower() in first_get.url.lower()
        assert "{" not in first_get.url and "%7B" not in first_get.url

    def test_slaitems_paging_follows_next_link(self, backend):
        """slaitems beyond the server page size are still part of the plan."""
        wf3 = "33333333-3333-3333-3333-333333333333"
        next_url = backend.url_for("slaitems") + "?$skiptoken=page2"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"slas({_SLA_ID})"), json=_sla_row())
            m.get(backend.url_for("slaitems"), [
                {"json": {**_items_payload(), "@odata.nextLink": next_url}},
            ])
            for wid in (_WF1, _WF2, wf3):
                m.get(backend.url_for(f"workflows({wid})"),
                      json=_wf_row(wid, 1))
            # second page registered on the same matcher via query string
            m.get(next_url, json={"value": [
                {"slaitemid": "0000000c-000c-000c-000c-00000000000c",
                 "name": "Page 2 item", "_workflowid_value": wf3}]})
            m.patch(backend.url_for(f"slas({_SLA_ID})"), status_code=204)
            result = activate_sla(backend, _SLA_ID)

        assert [w["workflow_id"] for w in result["workflows"]] == [_WF1, _WF2, wf3]


# ── Command layer ────────────────────────────────────────────────────────

import json

from click.testing import CliRunner

from crm.utils.d365_backend import ConnectionProfile as _Profile


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    session_mod.save_profile(_Profile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


def _success_result():
    return {
        "sla_id": _SLA_ID, "name": "Gold SLA", "sla_activated": True,
        "workflows": [{"workflow_id": _WF1, "name": "wf1", "status": "activated"}],
    }


def _failure_result():
    return {
        "sla_id": _SLA_ID, "name": "Gold SLA", "sla_activated": False,
        "ui_activation_required": True,
        "workflows": [
            {"workflow_id": _WF1, "name": "wf1", "status": "failed",
             "error": "This workflow has errors. ErrorMap Details: "
                      "{ConditionBranchStep2: InvalidEntity}",
             "errors": [{"step": "ConditionBranchStep2",
                         "errors": ["InvalidEntity"]}]},
        ],
    }


class TestSlaActivateCommand:
    def test_json_happy_path(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import sla as sla_cmd
        monkeypatch.setattr(sla_cmd.sla_mod, "activate_sla",
                            lambda backend, sid, **kw: _success_result())
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "sla", "activate", _SLA_ID])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["data"]["sla_activated"] is True
        assert envelope["data"]["workflows"][0]["status"] == "activated"

    def test_compile_failure_exits_nonzero_with_ui_message(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import sla as sla_cmd
        monkeypatch.setattr(sla_cmd.sla_mod, "activate_sla",
                            lambda backend, sid, **kw: _failure_result())
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "sla", "activate", _SLA_ID])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert "Service Level Agreements" in envelope["error"]
        # structured per-workflow report still ships in data
        assert envelope["data"]["workflows"][0]["errors"][0]["step"] == \
            "ConditionBranchStep2"

    def test_invalid_guid_fails_before_backend(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.cli import CLIContext

        def _no_backend(self):
            raise AssertionError("ctx.backend() must not be called")
        monkeypatch.setattr(CLIContext, "backend", _no_backend)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "sla", "activate", "not-a-guid"])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert "Invalid GUID" in envelope["error"]
