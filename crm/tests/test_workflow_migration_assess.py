"""Tests for the workflow-to-flow migration readiness assessment (issue #199).

Heuristic anchored to the MS Learn capability table "Replace classic Dataverse
workflows with flows" (verified 2026-06-10): cloud flows cannot run
synchronously (real-time), cannot use wait conditions, and cannot run custom
(non-out-of-box) workflow activities. Detection markers were ground-truthed
against live category-0 workflows on Dataverse online and on-prem v9.1.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.core import workflow
from crm.utils.d365_backend import ConnectionProfile, D365Backend


def _row(**over):
    base = {
        "workflowid": "11111111-2222-3333-4444-555555555555",
        "name": "Plain background",
        "primaryentity": "account",
        "mode": 0,
        "statecode": 1,
        "xaml": "<Activity />",
    }
    base.update(over)
    return base


class TestAssessOneWorkflow:
    def test_realtime_workflow_is_blocked(self):
        row = _row(mode=1)
        out = workflow.assess_workflow_migration(row)
        assert out["verdict"] == "blocked"
        assert out["blockers"] == [workflow.MIGRATION_BLOCKER_REAL_TIME]
        assert out["mode"] == "realtime"

    def test_wait_condition_workflow_is_blocked(self):
        # Classic wait/wait-timeout steps compile to a `Postpone` activity.
        xaml = '<Activity><mxswa:Postpone DisplayName="WaitStep4" /></Activity>'
        out = workflow.assess_workflow_migration(_row(xaml=xaml))
        assert out["verdict"] == "blocked"
        assert out["blockers"] == [workflow.MIGRATION_BLOCKER_WAIT]

    def test_step_named_wait_without_postpone_is_not_blocked(self):
        # A step merely *named* "Wait ..." (DisplayName) is not a wait condition.
        xaml = '<Activity><mxswa:CreateEntity DisplayName="Wait for owner" /></Activity>'
        out = workflow.assess_workflow_migration(_row(xaml=xaml))
        assert out["verdict"] == "ready"
        assert out["blockers"] == []

    def test_custom_activity_workflow_is_blocked(self):
        xaml = (
            '<Activity><mxswa:ActivityReference '
            'AssemblyQualifiedName="Contoso.Workflows.DoThing, Contoso.Workflows, '
            'Version=1.0.0.0, Culture=neutral, PublicKeyToken=abc" /></Activity>'
        )
        out = workflow.assess_workflow_migration(_row(xaml=xaml))
        assert out["verdict"] == "blocked"
        assert out["blockers"] == [workflow.MIGRATION_BLOCKER_CUSTOM_ACTIVITY]

    def test_oob_activity_reference_is_not_custom(self):
        # Out-of-box activities live in Microsoft.Crm.Workflow — never a blocker.
        xaml = (
            '<Activity><mxswa:ActivityReference '
            'AssemblyQualifiedName="Microsoft.Crm.Workflow.Activities.CreateEntity, '
            'Microsoft.Crm.Workflow, Version=9.0.0.0, Culture=neutral, '
            'PublicKeyToken=31bf3856ad364e35" /></Activity>'
        )
        out = workflow.assess_workflow_migration(_row(xaml=xaml))
        assert out["verdict"] == "ready"
        assert out["blockers"] == []

    def test_plain_background_workflow_is_ready(self):
        out = workflow.assess_workflow_migration(_row())
        assert out["verdict"] == "ready"
        assert out["blockers"] == []
        assert out["mode"] == "background"
        assert out["state"] == "activated"

    def test_multiple_blockers_listed_in_order(self):
        xaml = (
            '<Activity><mxswa:Postpone /><mxswa:ActivityReference '
            'AssemblyQualifiedName="Contoso.X, Contoso.Workflows, Version=1.0" />'
            '</Activity>'
        )
        out = workflow.assess_workflow_migration(_row(mode=1, xaml=xaml))
        assert out["blockers"] == [
            workflow.MIGRATION_BLOCKER_REAL_TIME,
            workflow.MIGRATION_BLOCKER_WAIT,
            workflow.MIGRATION_BLOCKER_CUSTOM_ACTIVITY,
        ]


@pytest.fixture
def backend():
    profile = ConnectionProfile(
        name="testp", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", api_version="v9.2", verify_ssl=False,
    )
    return D365Backend(profile, password="pw", dry_run=False)


class TestAssessWorkflowMigrations:
    def test_filters_to_category0_definitions_and_assesses(self, backend):
        url = backend.url_for("workflows")
        with requests_mock.Mocker() as m:
            m.get(url, json={"value": [
                _row(workflowid="a" * 36, name="RT", mode=1),
                _row(workflowid="b" * 36, name="Plain"),
            ]})
            out = workflow.assess_workflow_migrations(backend)
            sent = m.request_history[0]
        # type=1 (definition) + category=0 filter is always applied
        assert "type eq 1" in sent.qs["$filter"][0]
        assert "category eq 0" in sent.qs["$filter"][0]
        assert [r["verdict"] for r in out] == ["blocked", "ready"]

    def test_entity_filter_applied(self, backend):
        url = backend.url_for("workflows")
        with requests_mock.Mocker() as m:
            m.get(url, json={"value": []})
            workflow.assess_workflow_migrations(backend, primary_entity="account")
            sent = m.request_history[0]
        assert "primaryentity eq 'account'" in sent.qs["$filter"][0]

    def test_follows_odata_nextlink_pagination(self, backend):
        url = backend.url_for("workflows")
        next_link = url + "?$skiptoken=page2"
        with requests_mock.Mocker() as m:
            m.get(url, json={
                "value": [_row(workflowid="a" * 36)],
                "@odata.nextLink": next_link,
            })
            m.get(next_link, json={"value": [_row(workflowid="b" * 36, mode=1)]})
            out = workflow.assess_workflow_migrations(backend)
        assert [r["id"] for r in out] == ["a" * 36, "b" * 36]
        assert out[1]["verdict"] == "blocked"


# ── Command-layer tests for `crm workflow migration-assess` ──────────────────

def _seed_profile(tmp_path, monkeypatch, *, name, auth_scheme):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    if auth_scheme == "oauth":
        prof = ConnectionProfile(
            name=name, url="https://org.crm.dynamics.com", domain="", username="",
            auth_scheme="oauth", tenant_id="t", client_id="c")
    else:
        prof = ConnectionProfile(
            name=name, url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice")
    session_mod.save_profile(prof)
    session_mod.save_profile_secret_plaintext(name, "pw")


_CANNED = [
    {"id": "a" * 36, "name": "RT", "primaryentity": "account", "state": "activated",
     "mode": "realtime", "verdict": "blocked", "blockers": ["real_time"]},
    {"id": "b" * 36, "name": "Plain", "primaryentity": "contact", "state": "draft",
     "mode": "background", "verdict": "ready", "blockers": []},
]


class TestMigrationAssessCommand:
    def _invoke(self, monkeypatch, tmp_path, *, auth_scheme, extra=None):
        _seed_profile(tmp_path, monkeypatch, name="t", auth_scheme=auth_scheme)
        from crm.commands import workflow as wf_cmd
        captured = {}

        def fake_assess(backend, *, primary_entity=None, **kw):
            captured["primary_entity"] = primary_entity
            return _CANNED

        monkeypatch.setattr(wf_cmd.workflow_mod, "assess_workflow_migrations", fake_assess)
        from crm.cli import cli
        args = ["--json", "--profile", "t", "workflow", "migration-assess"] + (extra or [])
        result = CliRunner().invoke(cli, args)
        return result, captured

    def test_online_emits_report_no_note(self, monkeypatch, tmp_path):
        result, _ = self._invoke(monkeypatch, tmp_path, auth_scheme="oauth")
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert [r["verdict"] for r in env["data"]] == ["blocked", "ready"]
        assert env["meta"]["count"] == 2
        assert "note" not in env["meta"]

    def test_onprem_carries_advisory_note(self, monkeypatch, tmp_path):
        result, _ = self._invoke(monkeypatch, tmp_path, auth_scheme="ntlm")
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert "online" in env["meta"]["note"].lower()

    def test_entity_flag_forwarded(self, monkeypatch, tmp_path):
        result, captured = self._invoke(
            monkeypatch, tmp_path, auth_scheme="oauth", extra=["--entity", "account"])
        assert result.exit_code == 0
        assert captured["primary_entity"] == "account"
