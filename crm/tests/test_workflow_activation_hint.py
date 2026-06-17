"""Command-layer tests for the workflow activation-record hint (issue #160)."""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.utils.d365_backend import ConnectionProfile, D365Error


_WF_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_PARENT_GUID = "11111111-2222-3333-4444-555555555555"


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    session_mod.save_profile(ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


class TestWorkflowActivationHintWiring:
    def test_deactivate_hint_reaches_envelope(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod,
            "set_workflow_state",
            lambda backend, wid, **kw: (_ for _ in ()).throw(
                D365Error("Cannot update a workflow activation.", status=400, code="0x80045003")
            ),
        )
        monkeypatch.setattr(
            wf_cmd.workflow_mod,
            "activation_record_hint",
            lambda backend, wid, exc: f"hint: use parent {_PARENT_GUID}",
        )

        from crm.cli import cli
        result = CliRunner().invoke(cli, ["--json", "--profile", "t", "workflow", "deactivate", _WF_ID, "--yes"])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["meta"]["code"] == "0x80045003"
        assert _PARENT_GUID in envelope["meta"]["hint"]

    def test_activate_hint_reaches_envelope(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod,
            "set_workflow_state",
            lambda backend, wid, **kw: (_ for _ in ()).throw(
                D365Error("Cannot update a workflow activation.", status=400, code="0x80045003")
            ),
        )
        monkeypatch.setattr(
            wf_cmd.workflow_mod,
            "activation_record_hint",
            lambda backend, wid, exc: f"hint: use parent {_PARENT_GUID}",
        )

        from crm.cli import cli
        result = CliRunner().invoke(cli, ["--json", "--profile", "t", "workflow", "activate", _WF_ID])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["meta"]["code"] == "0x80045003"
        assert _PARENT_GUID in envelope["meta"]["hint"]

    def test_non_activation_error_skips_hint_lookup(self, monkeypatch, tmp_path):
        """A non-0x80045003 error must NOT trigger the hint lookup — the gate
        keeps the second ctx.backend() off the credential-failure path."""
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod,
            "set_workflow_state",
            lambda backend, wid, **kw: (_ for _ in ()).throw(
                D365Error("Workflow not found.", status=404, code="0x80040217")
            ),
        )
        called = {"hint": False}

        def _spy(backend, wid, exc):
            called["hint"] = True
            return None

        monkeypatch.setattr(wf_cmd.workflow_mod, "activation_record_hint", _spy)

        from crm.cli import cli
        result = CliRunner().invoke(cli, ["--json", "--profile", "t", "workflow", "deactivate", _WF_ID, "--yes"])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["meta"]["code"] == "0x80040217"
        assert "hint" not in envelope["meta"]
        assert called["hint"] is False


class TestWorkflowAutoResolveNoteWiring:
    """The redirect note emitted when set_workflow_state resolved an
    activation-record GUID to its parent definition (issue #170)."""

    @staticmethod
    def _info(resolved: bool, *, activated: bool) -> dict:
        return {
            "workflow_id": _PARENT_GUID if resolved else _WF_ID,
            "activated": activated,
            "statecode": 1 if activated else 0,
            "statuscode": 2 if activated else 1,
            "resolved_from_activation_id": _WF_ID if resolved else None,
        }

    def test_deactivate_redirect_note_in_json_envelope(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "set_workflow_state",
            lambda backend, wid, **kw: self._info(True, activated=False),
        )
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "deactivate", _WF_ID, "--yes"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["data"]["workflow_id"] == _PARENT_GUID
        assert envelope["data"]["resolved_from_activation_id"] == _WF_ID
        note = envelope["meta"]["note"]
        assert _PARENT_GUID in note and _WF_ID in note

    def test_activate_redirect_note_in_json_envelope(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "set_workflow_state",
            lambda backend, wid, **kw: self._info(True, activated=True),
        )
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "activate", _WF_ID])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        note = envelope["meta"]["note"]
        assert _PARENT_GUID in note and _WF_ID in note

    def test_redirect_note_renders_in_human_mode(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "set_workflow_state",
            lambda backend, wid, **kw: self._info(True, activated=False),
        )
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--profile", "t", "workflow", "deactivate", _WF_ID, "--yes"])
        assert result.exit_code == 0
        assert "Operated on parent definition" in result.output
        assert _PARENT_GUID in result.output

    def test_no_note_without_redirect(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd

        monkeypatch.setattr(
            wf_cmd.workflow_mod, "set_workflow_state",
            lambda backend, wid, **kw: self._info(False, activated=False),
        )
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "deactivate", _WF_ID, "--yes"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert "note" not in (envelope.get("meta") or {})


class TestEntityDeleteActivationHintWiring:
    """`entity delete` against a workflow activation GUID (issue #161)."""

    def test_delete_hint_reaches_envelope(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as entity_cmd
        from crm.core import workflow as workflow_mod

        monkeypatch.setattr(
            entity_cmd.entity_mod,
            "delete",
            lambda backend, entity_set, record_id, **kw: (_ for _ in ()).throw(
                D365Error("Cannot delete a workflow activation.", status=400, code="0x80045004")
            ),
        )
        # entity delete lazy-imports workflow on the error path, so patch the
        # source module (crm.core.workflow), not a command-level alias.
        monkeypatch.setattr(
            workflow_mod,
            "activation_delete_hint",
            lambda backend, wid, exc: f"hint: crm workflow deactivate {_PARENT_GUID}",
        )

        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "entity", "delete", "workflows", _WF_ID, "--yes"])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["meta"]["code"] == "0x80045004"
        assert _PARENT_GUID in envelope["meta"]["hint"]

    def test_non_activation_delete_skips_hint_lookup(self, monkeypatch, tmp_path):
        """A non-0x80045004 delete failure must NOT trigger the hint lookup —
        the gate keeps the resolver's extra GET off every other delete."""
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as entity_cmd
        from crm.core import workflow as workflow_mod

        monkeypatch.setattr(
            entity_cmd.entity_mod,
            "delete",
            lambda backend, entity_set, record_id, **kw: (_ for _ in ()).throw(
                D365Error("Record not found.", status=404, code="0x80040217")
            ),
        )
        called = {"hint": False}

        def _spy(backend, wid, exc):
            called["hint"] = True
            return None

        monkeypatch.setattr(workflow_mod, "activation_delete_hint", _spy)

        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "entity", "delete", "contacts", _WF_ID, "--yes"])
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["meta"]["code"] == "0x80040217"
        assert "hint" not in envelope["meta"]
        assert called["hint"] is False
