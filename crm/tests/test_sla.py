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


_NEW_SLA = "7a7a7a7a-1111-2222-3333-444444444444"
_NEW_ITEM = "8b8b8b8b-1111-2222-3333-555555555555"
_BUSINESS_HOURS = "9c9c9c9c-1111-2222-3333-666666666666"
_FETCH = '<fetch><entity name="incident"><filter><condition attribute="prioritycode" operator="eq" value="1"/></filter></entity></fetch>'
_SUCCESS = '<fetch><entity name="incident"><filter><condition attribute="statecode" operator="eq" value="1"/></filter></entity></fetch>'


def _created_header(entity_set, guid):
    return {"OData-EntityId":
            f"https://crm.contoso.local/contoso/api/data/v9.1/{entity_set}({guid})"}


def _incident_metadata(sla_enabled):
    """A minimal EntityDefinitions body whose IsSLAEnabled BooleanManagedProperty
    reflects `sla_enabled`."""
    return {
        "LogicalName": "incident",
        "IsSLAEnabled": {
            "Value": sla_enabled,
            "CanBeChanged": True,
            "ManagedPropertyLogicalName": "isslaenabled",
        },
    }


class TestCreateSla:
    def test_posts_record_and_reports_already_enabled(self, backend):
        from crm.core.sla import create_sla
        with requests_mock.Mocker() as m:
            post = m.post(backend.url_for("slas"), status_code=204,
                          headers=_created_header("slas", _NEW_SLA))
            m.get(backend.url_for("EntityDefinitions(LogicalName='incident')"),
                  json=_incident_metadata(True))
            result = create_sla(backend, name="Gold SLA", entity="incident",
                                applicable_from="createdon", solution="MySolution")

        assert result["created"] is True
        assert result["slaid"] == _NEW_SLA
        assert result["sla_enabled"] == "already"
        body = post.last_request.json()
        assert body["name"] == "Gold SLA"
        assert body["objecttypecode"] == "incident"
        assert body["applicablefrom"] == "createdon"
        # --solution is plumbed as the MSCRM.SolutionUniqueName write header.
        assert post.last_request.headers["MSCRM.SolutionUniqueName"] == "MySolution"

    def test_business_hours_bound_as_lookup(self, backend):
        from crm.core.sla import create_sla
        with requests_mock.Mocker() as m:
            post = m.post(backend.url_for("slas"), status_code=204,
                          headers=_created_header("slas", _NEW_SLA))
            m.get(backend.url_for("EntityDefinitions(LogicalName='incident')"),
                  json=_incident_metadata(True))
            create_sla(backend, name="Gold", entity="incident",
                       business_hours_id=_BUSINESS_HOURS)
        body = post.last_request.json()
        assert body["businesshoursid@odata.bind"] == f"/calendars({_BUSINESS_HOURS})"

    def test_enables_sla_on_entity_when_not_enabled(self, backend):
        """When the target entity is not SLA-enabled, create flips IsSLAEnabled
        via a metadata PUT and publishes."""
        from crm.core.sla import create_sla
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("slas"), status_code=204,
                   headers=_created_header("slas", _NEW_SLA))
            md_url = backend.url_for("EntityDefinitions(LogicalName='incident')")
            m.get(md_url, json=_incident_metadata(False))
            put = m.put(md_url, status_code=204)
            m.post(backend.url_for("PublishAllXml"), status_code=204)
            result = create_sla(backend, name="Gold", entity="incident")

        assert result["sla_enabled"] == "set"
        assert put.call_count == 1
        assert put.last_request.json()["IsSLAEnabled"]["Value"] is True

    def test_dry_run_previews_without_posting(self, profile):
        from crm.core.sla import create_sla
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.get(dry.url_for("EntityDefinitions(LogicalName='incident')"),
                  json=_incident_metadata(False))
            result = create_sla(dry, name="Gold", entity="incident")

        assert result["_dry_run"] is True
        assert result["would_create"]["entity_set"] == "slas"
        assert result["would_create"]["body"]["objecttypecode"] == "incident"
        assert result["sla_enabled"] == "would_set"
        assert _requests(m, "POST") == []
        assert _requests(m, "PUT") == []

    def test_requires_name_and_entity(self, backend):
        from crm.core.sla import create_sla
        with pytest.raises(D365Error, match="name is required"):
            create_sla(backend, name="", entity="incident")
        with pytest.raises(D365Error, match="entity is required"):
            create_sla(backend, name="Gold", entity="")


class TestAddKpi:
    def test_posts_slaitem_with_conditions(self, backend):
        from crm.core.sla import add_kpi
        with requests_mock.Mocker() as m:
            post = m.post(backend.url_for("slaitems"), status_code=204,
                          headers=_created_header("slaitems", _NEW_ITEM))
            result = add_kpi(backend, sla_id=_SLA_ID, kpi="resolvebykpiid",
                             applicable_when=_FETCH, success_criteria=_SUCCESS,
                             solution="MySolution")

        assert result["created"] is True
        assert result["slaitemid"] == _NEW_ITEM
        assert result["sla_id"] == _SLA_ID
        body = post.last_request.json()
        assert body["slaid@odata.bind"] == f"/slas({_SLA_ID})"
        assert body["relatedfield"] == "resolvebykpiid"
        assert body["applicablewhenxml"] == _FETCH
        assert body["successconditionsxml"] == _SUCCESS
        # name defaults to the KPI field when not given
        assert body["name"] == "resolvebykpiid"
        # --solution is plumbed as the MSCRM.SolutionUniqueName write header.
        assert post.last_request.headers["MSCRM.SolutionUniqueName"] == "MySolution"

    def test_explicit_name_overrides_kpi_default(self, backend):
        from crm.core.sla import add_kpi
        with requests_mock.Mocker() as m:
            post = m.post(backend.url_for("slaitems"), status_code=204,
                          headers=_created_header("slaitems", _NEW_ITEM))
            add_kpi(backend, sla_id=_SLA_ID, kpi="resolvebykpiid", name="Resolve in 4h",
                    applicable_when=_FETCH, success_criteria=_SUCCESS)
        assert post.last_request.json()["name"] == "Resolve in 4h"

    def test_dry_run_previews_without_posting(self, profile):
        from crm.core.sla import add_kpi
        dry = D365Backend(profile, password="pw", dry_run=True)
        with requests_mock.Mocker() as m:
            m.post(dry.url_for("slaitems"), status_code=204)
            result = add_kpi(dry, sla_id=_SLA_ID, kpi="resolvebykpiid",
                             applicable_when=_FETCH, success_criteria=_SUCCESS)
        assert result["_dry_run"] is True
        assert result["would_create"]["entity_set"] == "slaitems"
        assert result["sla_id"] == _SLA_ID
        assert _requests(m, "POST") == []

    def test_requires_conditions(self, backend):
        from crm.core.sla import add_kpi
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="applicable_when is required"):
                add_kpi(backend, sla_id=_SLA_ID, kpi="k",
                        applicable_when="", success_criteria=_SUCCESS)
            with pytest.raises(D365Error, match="success_criteria is required"):
                add_kpi(backend, sla_id=_SLA_ID, kpi="k",
                        applicable_when=_FETCH, success_criteria="")
        assert m.request_history == []

    def test_invalid_sla_guid_fails_before_request(self, backend):
        from crm.core.sla import add_kpi
        with requests_mock.Mocker() as m:
            with pytest.raises(D365Error, match="Invalid GUID"):
                add_kpi(backend, sla_id="not-a-guid", kpi="k",
                        applicable_when=_FETCH, success_criteria=_SUCCESS)
        assert m.request_history == []


class TestSlaCreateCommand:
    def test_json_happy_path(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import sla as sla_cmd
        captured = {}

        def _fake_create(backend, **kw):
            captured.update(kw)
            return {"created": True, "slaid": _NEW_SLA, "name": kw["name"],
                    "entity": kw["entity"], "sla_enabled": "set"}
        monkeypatch.setattr(sla_cmd.sla_mod, "create_sla", _fake_create)
        from crm.cli import cli
        result = CliRunner().invoke(cli, [
            "--json", "--profile", "t", "sla", "create",
            "--name", "Gold SLA", "--entity", "incident"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["data"]["sla_enabled"] == "set"
        assert captured["name"] == "Gold SLA"
        assert captured["entity"] == "incident"


class TestSlaAddKpiCommand:
    def test_resolves_success_criteria_from_file(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        crit = tmp_path / "success.xml"
        crit.write_text(_SUCCESS, encoding="utf-8")
        from crm.commands import sla as sla_cmd
        captured = {}

        def _fake_add(backend, **kw):
            captured.update(kw)
            return {"created": True, "slaitemid": _NEW_ITEM, "sla_id": kw["sla_id"],
                    "name": kw["kpi"]}
        monkeypatch.setattr(sla_cmd.sla_mod, "add_kpi", _fake_add)
        from crm.cli import cli
        result = CliRunner().invoke(cli, [
            "--json", "--profile", "t", "sla", "add-kpi",
            "--sla", _SLA_ID, "--kpi", "resolvebykpiid",
            "--applicable-when", _FETCH,
            "--success-criteria-file", str(crit)])
        assert result.exit_code == 0, result.output
        assert captured["applicable_when"] == _FETCH
        assert captured["success_criteria"] == _SUCCESS

    def test_missing_applicable_when_is_usage_error(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.cli import cli
        result = CliRunner().invoke(cli, [
            "--json", "--profile", "t", "sla", "add-kpi",
            "--sla", _SLA_ID, "--kpi", "k", "--success-criteria", _SUCCESS])
        assert result.exit_code != 0
        assert "--applicable-when" in result.output


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
