"""Command-layer tests for `crm workflow delete` (issue #164)."""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.utils.d365_backend import ConnectionProfile, D365Error


_ACT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_DEF_ID = "11111111-2222-3333-4444-555555555555"


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    session_mod.save_profile(ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


def _target(resolved: bool) -> dict:
    return {
        "workflow_id": _DEF_ID,
        "name": "Auto-set Owner",
        "statecode": 1,
        "resolved_from_activation_id": _ACT_ID if resolved else None,
    }


def _delete_info(resolved: bool) -> dict:
    return {
        "deleted": True,
        "workflow_id": _DEF_ID,
        "name": "Auto-set Owner",
        "deactivated": True,
        "resolved_from_activation_id": _ACT_ID if resolved else None,
    }


class TestWorkflowDeleteCommand:
    def test_yes_json_success_with_redirect_note(self, monkeypatch, tmp_path):
        """--yes skips the prompt; the envelope carries the resolution note in
        meta and the delete result in data."""
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "resolve_delete_target",
            lambda backend, wid, **kw: _target(True),
        )
        monkeypatch.setattr(
            wf_cmd.workflow_mod, "delete_workflow",
            lambda backend, wid, **kw: _delete_info(True),
        )
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "delete", _ACT_ID, "--yes"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["data"]["deleted"] is True
        assert envelope["data"]["workflow_id"] == _DEF_ID
        note = envelope["meta"]["note"]
        assert _DEF_ID in note and _ACT_ID in note

    def test_no_note_when_definition_guid_passed(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "resolve_delete_target",
            lambda backend, wid, **kw: _target(False),
        )
        monkeypatch.setattr(
            wf_cmd.workflow_mod, "delete_workflow",
            lambda backend, wid, **kw: _delete_info(False),
        )
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "delete", _DEF_ID, "--yes"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert "note" not in (envelope.get("meta") or {})

    def test_prompt_names_resolved_definition_and_abort(self, monkeypatch, tmp_path):
        """Without --yes the prompt names the resolved definition (name + GUID +
        activation-record wording); declining aborts with the documented envelope
        and never calls delete."""
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "resolve_delete_target",
            lambda backend, wid, **kw: _target(True),
        )
        called = {"delete": False}

        def _spy(backend, wid, **kw):
            called["delete"] = True
            return _delete_info(True)

        monkeypatch.setattr(wf_cmd.workflow_mod, "delete_workflow", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--profile", "t", "workflow", "delete", _ACT_ID], input="n\n")
        assert result.exit_code != 0
        assert "Auto-set Owner" in result.output
        assert _DEF_ID in result.output
        assert "activation record" in result.output
        assert "aborted by user" in result.output
        assert called["delete"] is False

    def test_prompt_accept_proceeds_with_resolved_target(self, monkeypatch, tmp_path):
        """Confirming runs the delete; the pre-fetched target is passed through
        so the core does not resolve a second time."""
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        target = _target(True)
        monkeypatch.setattr(
            wf_cmd.workflow_mod, "resolve_delete_target",
            lambda backend, wid, **kw: target,
        )
        seen = {}

        def _spy(backend, wid, **kw):
            seen["resolved"] = kw.get("resolved")
            return _delete_info(True)

        monkeypatch.setattr(wf_cmd.workflow_mod, "delete_workflow", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--profile", "t", "workflow", "delete", _ACT_ID], input="y\n")
        assert result.exit_code == 0
        assert seen["resolved"] is target

    def test_resolve_failure_reaches_envelope(self, monkeypatch, tmp_path):
        """The parent-gone operational failure surfaces as a clean error
        envelope before any prompt."""
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "resolve_delete_target",
            lambda backend, wid, **kw: (_ for _ in ()).throw(
                D365Error(f"{wid} is an activation record with no live parent "
                          "definition; there is no supported Web API path to "
                          "delete it — use the D365 UI.")
            ),
        )
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "delete", _ACT_ID, "--yes"])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert "no live parent definition" in envelope["error"]
