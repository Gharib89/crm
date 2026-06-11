"""Tests for --category friendly-name support on `crm workflow list` (issue #204)."""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.core import workflow as workflow_mod
from crm.utils.d365_backend import ConnectionProfile


def _seed_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    prof = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice",
    )
    session_mod.save_profile(prof)
    session_mod.save_profile_secret_plaintext("t", "pw")


def _invoke(monkeypatch, tmp_path, args, captured):
    _seed_profile(tmp_path, monkeypatch)
    from crm.commands import workflow as wf_cmd
    from crm.cli import cli

    def fake_list(backend, *, category=None, **kw):
        captured["category"] = category
        return []

    monkeypatch.setattr(wf_cmd.workflow_mod, "list_workflows", fake_list)
    result = CliRunner().invoke(cli, ["--json", "--profile", "t", "workflow", "list"] + args)
    return result


class TestWorkflowCategoryFriendlyNames:
    def test_name_workflow_maps_to_0(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "workflow"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_WORKFLOW

    def test_name_dialog_maps_to_1(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "dialog"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_DIALOG

    def test_name_businessrule_maps_to_2(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "businessrule"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_BUSINESS_RULE

    def test_name_action_maps_to_3(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "action"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_ACTION

    def test_name_bpf_maps_to_4(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "bpf"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_BPF

    def test_name_flow_maps_to_5(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "flow"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_MODERN_FLOW

    def test_case_insensitive_BPF(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "BPF"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_BPF

    def test_case_insensitive_mixed(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "BusinessRule"], captured)
        assert result.exit_code == 0
        assert captured["category"] == workflow_mod.CATEGORY_BUSINESS_RULE

    def test_integer_passthrough_0(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "0"], captured)
        assert result.exit_code == 0
        assert captured["category"] == 0

    def test_integer_passthrough_arbitrary(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "99"], captured)
        assert result.exit_code == 0
        assert captured["category"] == 99

    def test_no_category_passes_none(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, [], captured)
        assert result.exit_code == 0
        assert captured["category"] is None

    def test_invalid_name_exits_2(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "invalid"], captured)
        assert result.exit_code == 2

    def test_invalid_name_message_lists_names(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "invalid"], captured)
        output = result.output
        for name in ("workflow", "dialog", "businessrule", "action", "bpf", "flow"):
            assert name in output

    def test_invalid_name_message_notes_integers(self, monkeypatch, tmp_path):
        captured = {}
        result = _invoke(monkeypatch, tmp_path, ["--category", "invalid"], captured)
        assert "integer" in result.output.lower()
