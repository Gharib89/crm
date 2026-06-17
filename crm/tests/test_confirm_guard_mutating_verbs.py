"""Command-layer tests: `entity disassociate`, `entity clear-lookup`, and
`workflow deactivate` route through the shared destructive-confirm guard (#348).

Each verb mutates state (deletes a relationship row / clears a lookup via
DELETE /$ref / flips a workflow's statecode) and must behave like the existing
guarded deletes: expose `--yes`, prompt on a TTY, proceed with `--yes`, and abort
on a non-TTY without `--yes` with the documented ``{"ok": false, "error":
"aborted by user"}`` envelope and a non-zero exit — never touching the backend.
"""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.utils.d365_backend import ConnectionProfile


_REC = "11111111-1111-1111-1111-111111111111"


def _envelope(output: str) -> dict:
    """Extract the JSON envelope from `output`.

    On a non-TTY abort `click.confirm` still writes its prompt to stdout ahead of
    the emitted envelope (shared-helper behavior, identical to `entity delete`),
    so slice from the first brace before parsing.
    """
    return json.loads(output[output.index("{"):])


def _seed_profile(tmp_path, monkeypatch):
    """Isolate CRM_HOME and seed an NTLM profile + plaintext secret named 't'."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    from crm.core import session as session_mod
    session_mod.save_profile(ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice"))
    session_mod.save_profile_secret_plaintext("t", "pw")


class TestEntityDisassociateGuard:
    def test_yes_skips_prompt_and_proceeds(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as ent_cmd
        called = {"hit": False}

        def _spy(backend, *a, **kw):
            called["hit"] = True
            return {"disassociated": True}

        monkeypatch.setattr(ent_cmd.entity_mod, "disassociate", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "entity", "disassociate",
                  "accounts", _REC, "primarycontactid", "--yes"])
        assert result.exit_code == 0
        assert called["hit"] is True
        assert json.loads(result.output)["ok"] is True

    def test_no_tty_without_yes_aborts(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as ent_cmd
        called = {"hit": False}

        def _spy(backend, *a, **kw):
            called["hit"] = True
            return {"disassociated": True}

        monkeypatch.setattr(ent_cmd.entity_mod, "disassociate", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "entity", "disassociate",
                  "accounts", _REC, "primarycontactid"])
        assert result.exit_code != 0
        assert _envelope(result.output) == {"ok": False, "error": "aborted by user"}
        assert called["hit"] is False

    def test_prompt_decline_aborts(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as ent_cmd
        called = {"hit": False}

        def _spy(backend, *a, **kw):
            called["hit"] = True
            return {"disassociated": True}

        monkeypatch.setattr(ent_cmd.entity_mod, "disassociate", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--profile", "t", "entity", "disassociate",
                  "accounts", _REC, "primarycontactid"], input="n\n")
        assert result.exit_code != 0
        assert "aborted by user" in result.output
        assert called["hit"] is False


class TestEntityClearLookupGuard:
    def test_yes_skips_prompt_and_proceeds(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as ent_cmd
        called = {"hit": False}

        def _spy(backend, *a, **kw):
            called["hit"] = True
            return {"cleared": True}

        monkeypatch.setattr(ent_cmd.entity_mod, "clear_lookup", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "entity", "clear-lookup",
                  "accounts", _REC, "primarycontactid", "--yes"])
        assert result.exit_code == 0
        assert called["hit"] is True
        assert json.loads(result.output)["ok"] is True

    def test_no_tty_without_yes_aborts(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as ent_cmd
        called = {"hit": False}

        def _spy(backend, *a, **kw):
            called["hit"] = True
            return {"cleared": True}

        monkeypatch.setattr(ent_cmd.entity_mod, "clear_lookup", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "entity", "clear-lookup",
                  "accounts", _REC, "primarycontactid"])
        assert result.exit_code != 0
        assert _envelope(result.output) == {"ok": False, "error": "aborted by user"}
        assert called["hit"] is False

    def test_prompt_decline_aborts(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import entity as ent_cmd
        called = {"hit": False}

        def _spy(backend, *a, **kw):
            called["hit"] = True
            return {"cleared": True}

        monkeypatch.setattr(ent_cmd.entity_mod, "clear_lookup", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--profile", "t", "entity", "clear-lookup",
                  "accounts", _REC, "primarycontactid"], input="n\n")
        assert result.exit_code != 0
        assert "aborted by user" in result.output
        assert called["hit"] is False


class TestWorkflowDeactivateGuard:
    def test_yes_skips_prompt_and_proceeds(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd
        called = {"hit": False}

        def _spy(backend, wid, **kw):
            called["hit"] = True
            return {"workflow_id": wid, "statecode": 0}

        monkeypatch.setattr(wf_cmd.workflow_mod, "set_workflow_state", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "deactivate", _REC, "--yes"])
        assert result.exit_code == 0
        assert called["hit"] is True
        assert json.loads(result.output)["ok"] is True

    def test_no_tty_without_yes_aborts(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd
        called = {"hit": False}

        def _spy(backend, wid, **kw):
            called["hit"] = True
            return {"workflow_id": wid, "statecode": 0}

        monkeypatch.setattr(wf_cmd.workflow_mod, "set_workflow_state", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "t", "workflow", "deactivate", _REC])
        assert result.exit_code != 0
        assert _envelope(result.output) == {"ok": False, "error": "aborted by user"}
        assert called["hit"] is False

    def test_prompt_decline_aborts(self, monkeypatch, tmp_path):
        _seed_profile(tmp_path, monkeypatch)
        from crm.commands import workflow as wf_cmd
        called = {"hit": False}

        def _spy(backend, wid, **kw):
            called["hit"] = True
            return {"workflow_id": wid, "statecode": 0}

        monkeypatch.setattr(wf_cmd.workflow_mod, "set_workflow_state", _spy)
        from crm.cli import cli
        result = CliRunner().invoke(
            cli, ["--profile", "t", "workflow", "deactivate", _REC], input="n\n")
        assert result.exit_code != 0
        assert "aborted by user" in result.output
        assert called["hit"] is False
