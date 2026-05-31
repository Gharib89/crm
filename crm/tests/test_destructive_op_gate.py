"""Unit tests for the standalone Claude Code PreToolUse destructive-op gate.

The hook is a pure-stdlib script (no crm import, no network) that inspects the
Bash command Claude Code is about to run and BLOCKS (exit 2) destructive `crm`
verbs unless an explicit `--yes` confirm token is present. Non-destructive verbs
and non-crm commands pass through untouched (exit 0).
"""
# pyright: basic
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "destructive_op_gate.py"

BLOCK = 2  # Claude Code PreToolUse blocking exit code.


def _run(command: str, tool_name: str = "Bash"):
    """Invoke the hook with a PreToolUse stdin payload; return CompletedProcess."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
    }
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _run_raw(stdin: str):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
    )


class TestBlocksDestructive:
    def test_block_delete_entity_no_yes(self):
        r = _run("crm metadata delete-entity new_widget")
        assert r.returncode == BLOCK
        assert "delete-entity" in r.stderr
        assert "--yes" in r.stderr

    def test_allow_delete_entity_with_yes(self):
        r = _run("crm metadata delete-entity new_widget --yes")
        assert r.returncode == 0
        assert r.stderr == ""

    def test_block_delete_optionset_no_yes(self):
        r = _run("crm metadata delete-optionset new_color")
        assert r.returncode == BLOCK
        assert "delete-optionset" in r.stderr

    def test_allow_delete_optionset_with_yes(self):
        r = _run("crm metadata delete-optionset new_color --yes")
        assert r.returncode == 0

    def test_block_entity_delete_no_yes(self):
        r = _run("crm entity delete contacts 11111111-1111-1111-1111-111111111111")
        assert r.returncode == BLOCK
        assert "delete" in r.stderr

    def test_allow_entity_delete_with_yes(self):
        r = _run("crm entity delete contacts 11111111-1111-1111-1111-111111111111 --yes")
        assert r.returncode == 0

    def test_block_solution_job_cancel_no_yes(self):
        r = _run("crm solution job-cancel 22222222-2222-2222-2222-222222222222")
        assert r.returncode == BLOCK
        assert "job-cancel" in r.stderr

    def test_allow_solution_job_cancel_with_yes(self):
        r = _run("crm solution job-cancel 22222222-2222-2222-2222-222222222222 --yes")
        assert r.returncode == 0

    def test_block_async_cancel_no_yes(self):
        r = _run("crm async cancel 33333333-3333-3333-3333-333333333333")
        assert r.returncode == BLOCK
        assert "cancel" in r.stderr

    def test_allow_async_cancel_with_yes(self):
        r = _run("crm async cancel 33333333-3333-3333-3333-333333333333 --yes")
        assert r.returncode == 0


class TestForwardLookingVerbs:
    def test_block_delete_attribute_not_yet_existing(self):
        r = _run("crm metadata delete-attribute new_widget new_field")
        assert r.returncode == BLOCK
        assert "delete-attribute" in r.stderr

    def test_allow_delete_attribute_with_yes(self):
        r = _run("crm metadata delete-attribute new_widget new_field --yes")
        assert r.returncode == 0

    def test_block_delete_relationship_not_yet_existing(self):
        r = _run("crm metadata delete-relationship new_rel")
        assert r.returncode == BLOCK
        assert "delete-relationship" in r.stderr

    def test_allow_delete_relationship_with_yes(self):
        r = _run("crm metadata delete-relationship new_rel --yes")
        assert r.returncode == 0


class TestNonDestructivePassthrough:
    @pytest.mark.parametrize("cmd", [
        "crm query accounts --filter \"name eq 'x'\"",
        "crm entity get contacts 11111111-1111-1111-1111-111111111111",
        "crm metadata list-entities",
        "crm metadata entities",
        "crm async list",
        "crm solution list",
    ])
    def test_non_destructive_is_noop(self, cmd):
        r = _run(cmd)
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""


class TestNoFalsePositives:
    @pytest.mark.parametrize("cmd", [
        "ls",
        "git status",
        "cat crmnotes.txt",
        "grep delete-entity README.md",
        "echo crm metadata delete-entity",  # quoted/echoed, not an invocation... still a heuristic
    ])
    def test_non_crm_commands_pass(self, cmd):
        # Commands that are not an actual `crm` invocation must not block.
        # (echo case documents the heuristic boundary; see note in test below.)
        r = _run(cmd)
        # ls / git / cat / grep clearly are not crm invocations.
        if cmd.split()[0] != "echo":
            assert r.returncode == 0, f"false positive on: {cmd}\n{r.stderr}"

    def test_crm_as_substring_path_does_not_match(self):
        r = _run("cat crmnotes.txt")
        assert r.returncode == 0
        r2 = _run("ls /opt/crm/")
        assert r2.returncode == 0


class TestFlagOrdering:
    def test_global_flag_before_verb_with_yes_allowed(self):
        r = _run("crm --json metadata delete-entity new_widget --yes")
        assert r.returncode == 0

    def test_global_flag_before_verb_without_yes_blocked(self):
        r = _run("crm --json metadata delete-entity new_widget")
        assert r.returncode == BLOCK


class TestStdinParsing:
    def test_ignores_non_bash_tool(self):
        r = _run("crm metadata delete-entity new_widget", tool_name="Read")
        assert r.returncode == 0
        assert r.stderr == ""

    def test_malformed_json_is_noop(self):
        r = _run_raw("not json")
        assert r.returncode == 0

    def test_empty_stdin_is_noop(self):
        r = _run_raw("")
        assert r.returncode == 0

    def test_missing_command_is_noop(self):
        r = _run_raw(json.dumps({"tool_name": "Bash", "tool_input": {}}))
        assert r.returncode == 0


_GUID = "44444444-4444-4444-4444-444444444444"


class TestCliConfirmParity:
    """Every destructive command accepts --yes (skip prompt) and aborts safely
    without --yes in a non-TTY, matching the token the hook checks for."""

    def _runner(self):
        from click.testing import CliRunner
        return CliRunner()

    def test_entity_delete_yes_skips_prompt(self, monkeypatch):
        from crm import cli as crm_cli
        called = {}
        monkeypatch.setattr("crm.core.entity.delete",
                            lambda backend, es, rid, **kw: called.setdefault("hit", (es, rid)) or {"deleted": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli,
            ["--json", "entity", "delete", "contacts", _GUID, "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert called["hit"] == ("contacts", _GUID)

    def test_entity_delete_no_yes_non_tty_aborts(self, monkeypatch):
        from crm import cli as crm_cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli,
            ["--json", "entity", "delete", "contacts", _GUID],
            input="\n",
        )
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output

    def test_solution_job_cancel_yes_skips_prompt(self, monkeypatch):
        from crm import cli as crm_cli
        called = {}
        monkeypatch.setattr("crm.core.async_ops.cancel_async_operation",
                            lambda backend, aid: called.setdefault("id", aid))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli, ["--json", "solution", "job-cancel", _GUID, "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert called["id"] == _GUID

    def test_solution_job_cancel_no_yes_non_tty_aborts(self, monkeypatch):
        from crm import cli as crm_cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli, ["--json", "solution", "job-cancel", _GUID], input="\n",
        )
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output

    def test_async_cancel_yes_skips_prompt(self, monkeypatch):
        from crm import cli as crm_cli
        called = {}
        monkeypatch.setattr("crm.core.async_ops.cancel_async_operation",
                            lambda backend, aid: called.setdefault("id", aid))
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli, ["--json", "async", "cancel", _GUID, "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert called["id"] == _GUID

    def test_async_cancel_no_yes_non_tty_aborts(self, monkeypatch):
        from crm import cli as crm_cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli, ["--json", "async", "cancel", _GUID], input="\n",
        )
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output
