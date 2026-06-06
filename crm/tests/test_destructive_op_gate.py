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

    def test_block_solution_import_no_yes(self):
        r = _run("crm solution import pkg.zip")
        assert r.returncode == BLOCK
        assert "import" in r.stderr

    def test_allow_solution_import_with_yes(self):
        r = _run("crm solution import pkg.zip --yes")
        assert r.returncode == 0

    def test_block_solution_remove_component_no_yes(self):
        r = _run("crm solution remove-component --solution CRMWorx --type 61 "
                 "--id 33333333-3333-3333-3333-333333333333")
        assert r.returncode == BLOCK
        assert "remove-component" in r.stderr

    def test_allow_solution_remove_component_with_yes(self):
        r = _run("crm solution remove-component --solution CRMWorx --type 61 "
                 "--id 33333333-3333-3333-3333-333333333333 --yes")
        assert r.returncode == 0

    def test_allow_solution_add_component(self):
        # add-component is non-destructive: it must pass through without --yes.
        r = _run("crm solution add-component --solution CRMWorx --type 61 "
                 "--id 33333333-3333-3333-3333-333333333333")
        assert r.returncode == 0

    def test_block_async_cancel_no_yes(self):
        r = _run("crm async cancel 33333333-3333-3333-3333-333333333333")
        assert r.returncode == BLOCK
        assert "cancel" in r.stderr

    def test_allow_async_cancel_with_yes(self):
        r = _run("crm async cancel 33333333-3333-3333-3333-333333333333 --yes")
        assert r.returncode == 0

    def test_block_plugin_unregister_assembly_no_yes(self):
        r = _run("crm plugin unregister-assembly Contoso.Plugins")
        assert r.returncode == BLOCK
        assert "unregister-assembly" in r.stderr

    def test_allow_plugin_unregister_assembly_with_yes(self):
        r = _run("crm plugin unregister-assembly Contoso.Plugins --yes")
        assert r.returncode == 0
        assert r.stderr == ""

    def test_block_plugin_unregister_step_no_yes(self):
        r = _run("crm plugin unregister-step 'My Step'")
        assert r.returncode == BLOCK
        assert "unregister-step" in r.stderr

    def test_allow_plugin_unregister_step_with_yes(self):
        r = _run("crm plugin unregister-step 'My Step' --yes")
        assert r.returncode == 0

    def test_allow_plugin_register_assembly_passthrough(self):
        # register-assembly is non-destructive: it must pass through without --yes.
        r = _run("crm plugin register-assembly Contoso.Plugins.dll")
        assert r.returncode == 0
        assert r.stderr == ""


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


_ROLE_ID = "55555555-5555-5555-5555-555555555555"
_USER_ID = "66666666-6666-6666-6666-666666666666"


class TestAssignRole:
    """assign-role is in ROLE_VERBS — gated by verb name regardless of group."""

    def test_block_assign_role_no_yes(self):
        r = _run(f"crm security assign-role --to-user {_USER_ID} {_ROLE_ID}")
        assert r.returncode == BLOCK
        assert "assign-role" in r.stderr
        assert "--yes" in r.stderr

    def test_allow_assign_role_with_yes(self):
        r = _run(f"crm security assign-role --to-user {_USER_ID} {_ROLE_ID} --yes")
        assert r.returncode == 0
        assert r.stderr == ""

    def test_role_verb_gated_regardless_of_group(self):
        # ROLE_VERBS matches by verb name only — even an unrecognised group is blocked.
        r = _run(f"crm other-group assign-role {_ROLE_ID}")
        assert r.returncode == BLOCK
        assert "assign-role" in r.stderr


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
    ])
    def test_non_crm_commands_pass(self, cmd):
        # Commands that are not an actual `crm` invocation must not block.
        r = _run(cmd)
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

    @pytest.mark.parametrize("cmd", [
        "crm --profile prod metadata delete-entity new_widget",
        "crm --session foo entity delete contacts abc",
        "crm --log-level debug metadata delete-entity new_widget",
        "crm --log-format json-line metadata delete-optionset new_color",
        "crm --auth-scheme ntlm async cancel 33333333-3333-3333-3333-333333333333",
        "crm --password s3cret solution job-cancel 22222222-2222-2222-2222-222222222222",
    ])
    def test_value_option_before_verb_without_yes_blocked(self, cmd):
        # The option VALUE (prod/foo/debug/...) must not be mistaken for the
        # command group, which would silently defeat the gate.
        r = _run(cmd)
        assert r.returncode == BLOCK, f"gate bypassed by value option: {cmd}\n{r.stdout}"

    @pytest.mark.parametrize("cmd", [
        "crm --profile prod metadata delete-entity new_widget --yes",
        "crm --session foo entity delete contacts abc --yes",
        "crm --log-level debug metadata delete-entity new_widget --yes",
    ])
    def test_value_option_before_verb_with_yes_allowed(self, cmd):
        r = _run(cmd)
        assert r.returncode == 0, r.stderr

    def test_equals_form_value_option_blocked(self):
        # `--flag=value` already starts with `-`; ensure it is still dropped.
        r = _run("crm --profile=prod metadata delete-entity new_widget")
        assert r.returncode == BLOCK

    @pytest.mark.parametrize("cmd", [
        # `--yes` smuggled in as the VALUE of a value-taking global option must
        # NOT count as a confirm — the verb is still destructive and unconfirmed.
        "crm --profile --yes metadata delete-entity foo",
        "crm --password --yes entity delete contacts abc",
        "crm --session --yes solution job-cancel 22222222-2222-2222-2222-222222222222",
        "crm --log-level --yes metadata delete-optionset new_color",
    ])
    def test_yes_smuggled_as_option_value_blocked(self, cmd):
        r = _run(cmd)
        assert r.returncode == BLOCK, f"gate bypassed by --yes-as-option-value: {cmd}\n{r.stdout}"


class TestCompoundAndPathPrefix:
    def test_path_prefixed_crm_blocked(self):
        r = _run("/usr/local/bin/crm entity delete contacts abc")
        assert r.returncode == BLOCK
        assert "delete" in r.stderr

    def test_path_prefixed_crm_with_yes_allowed(self):
        r = _run("/usr/local/bin/crm entity delete contacts abc --yes")
        assert r.returncode == 0

    @pytest.mark.parametrize("op", ["&&", "||", ";", "|"])
    def test_crm_after_shell_operator_blocked(self, op):
        r = _run(f"true {op} crm entity delete contacts abc")
        assert r.returncode == BLOCK, f"gate bypassed after {op!r}\n{r.stdout}"

    def test_crm_after_operator_with_yes_allowed(self):
        r = _run("true && crm entity delete contacts abc --yes")
        assert r.returncode == 0

    def test_yes_in_other_segment_does_not_unblock(self):
        # A --yes on an unrelated sub-command must not unblock the destructive one.
        r = _run("echo --yes && crm entity delete contacts abc")
        assert r.returncode == BLOCK

    @pytest.mark.parametrize("cmd", [
        # Operator glued to the destructive sub-command: shlex would fold the
        # operator into a single token, so the gate must split the raw string.
        "crm query x|crm entity delete y",
        "crm query accounts &&crm entity delete y",
        "crm query x||crm entity delete y",
        "crm query x;crm entity delete y",
        # Command substitution — both `$(...)` and backtick forms.
        "$(crm entity delete x)",
        "echo $(crm metadata delete-entity new_widget)",
        "echo `crm entity delete contacts abc`",
        "`crm metadata delete-entity new_widget`",
    ])
    def test_glued_operator_and_command_substitution_blocked(self, cmd):
        r = _run(cmd)
        assert r.returncode == BLOCK, f"gate bypassed by glued operator/subst: {cmd}\n{r.stdout}"

    def test_glued_operator_with_yes_allowed(self):
        r = _run("crm query x|crm entity delete y --yes")
        assert r.returncode == 0, r.stderr


class TestSeparatorAndPrefixBypass:
    @pytest.mark.parametrize("cmd", [
        # Newline separates commands just like `;` — a destructive verb on any
        # line after the first must still be caught.
        "crm query x\ncrm entity delete contacts abc",
        "set -e\ncrm entity delete contacts abc",
        "crm query accounts\ncrm metadata delete-entity new_widget",
        "crm query x\r\ncrm entity delete contacts abc",
    ])
    def test_newline_separated_destructive_blocked(self, cmd):
        r = _run(cmd)
        assert r.returncode == BLOCK, f"gate bypassed by newline: {cmd!r}\n{r.stdout}"

    def test_newline_separated_with_yes_allowed(self):
        r = _run("crm query x\ncrm entity delete contacts abc --yes")
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("cmd", [
        # A leading shell variable-assignment prefix must not hide the crm verb.
        "FOO=1 crm entity delete contacts abc",
        "A=1 B=2 crm metadata delete-entity new_widget",
    ])
    def test_env_var_prefix_destructive_blocked(self, cmd):
        r = _run(cmd)
        assert r.returncode == BLOCK, f"gate bypassed by env-var prefix: {cmd!r}\n{r.stdout}"

    def test_env_var_prefix_with_yes_allowed(self):
        r = _run("FOO=1 crm entity delete contacts abc --yes")
        assert r.returncode == 0, r.stderr

    def test_env_var_prefix_non_destructive_passthrough(self):
        r = _run("FOO=1 crm query accounts")
        assert r.returncode == 0, r.stderr


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
        # No input => EOF on stdin, the real non-TTY agent scenario. click.confirm
        # raises Abort; the documented JSON envelope must still be emitted.
        result = self._runner().invoke(
            crm_cli.cli,
            ["--json", "entity", "delete", "contacts", _GUID],
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
            crm_cli.cli, ["--json", "solution", "job-cancel", _GUID],
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
            crm_cli.cli, ["--json", "async", "cancel", _GUID],
        )
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output

    def test_plugin_unregister_assembly_yes_skips_prompt(self, monkeypatch):
        from crm import cli as crm_cli
        called = {}
        monkeypatch.setattr(
            "crm.core.plugin.unregister_assembly",
            lambda backend, assembly: called.setdefault("a", assembly)
            or {"deleted": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli,
            ["--json", "plugin", "unregister-assembly", "Contoso.Plugins", "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert called["a"] == "Contoso.Plugins"

    def test_plugin_unregister_assembly_no_yes_non_tty_aborts(self, monkeypatch):
        from crm import cli as crm_cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli, ["--json", "plugin", "unregister-assembly", "Contoso.Plugins"],
        )
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output

    def test_plugin_unregister_step_yes_skips_prompt(self, monkeypatch):
        from crm import cli as crm_cli
        called = {}
        monkeypatch.setattr(
            "crm.core.plugin.unregister_step",
            lambda backend, step: called.setdefault("s", step)
            or {"deleted": True})
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli,
            ["--json", "plugin", "unregister-step", "My Step", "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert called["s"] == "My Step"

    def test_plugin_unregister_step_no_yes_non_tty_aborts(self, monkeypatch):
        from crm import cli as crm_cli
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
        result = self._runner().invoke(
            crm_cli.cli, ["--json", "plugin", "unregister-step", "My Step"],
        )
        assert result.exit_code == 1
        assert '"error": "aborted by user"' in result.output
