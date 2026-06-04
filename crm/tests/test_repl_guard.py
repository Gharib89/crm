"""Non-interactive REPL guard (#65): bare `crm` (no subcommand) must fail fast
instead of opening the interactive REPL when the caller is clearly
non-interactive — under --json, an explicit CRM_NO_REPL opt-out, or a non-TTY
stdin. It exits 2 (usage) with a message pointing at `crm --help`, and never
hangs or exits 0. Explicit `crm repl` and an interactive human are unaffected.
"""
# pyright: basic
from __future__ import annotations

import json

import click
from click.testing import CliRunner

from crm.cli import cli

_HELP_HINT = "crm --help"


def _stub_repl(recorder: list[bool]) -> click.Command:
    """A no-param `click.Command` standing in for the real REPL, so the launch
    tests assert the guard hands off to the REPL without entering the interactive
    prompt_toolkit loop (which raises NoConsoleScreenBufferError on Windows CI).
    A Command — not a bare callable — mirrors the production `ctx.invoke(repl)`
    path, where Click resolves params from the invoking context."""

    @click.command("repl")
    def _repl() -> None:
        recorder.append(True)

    return _repl


def test_bare_json_exits_2_with_usage_envelope(monkeypatch):
    """Bare `crm --json` → exit 2 with a parseable {ok: false} usage envelope
    pointing at `crm --help`, not a REPL drop."""
    monkeypatch.delenv("CRM_NO_REPL", raising=False)
    result = CliRunner().invoke(cli, ["--json"])
    assert result.exit_code == 2, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert _HELP_HINT in env["error"]
    assert result.stderr == "", "json path must write the envelope to stdout, not stderr"


def test_bare_crm_no_repl_env_exits_2_human(monkeypatch):
    """Bare `crm` with CRM_NO_REPL truthy → exit 2, plain-text error on stderr,
    nothing on stdout (no JSON envelope), pointing at `crm --help`. Click 8.2+
    keeps stdout/stderr separate, so we assert each stream explicitly."""
    monkeypatch.setenv("CRM_NO_REPL", "1")
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2
    assert _HELP_HINT in result.stderr
    assert result.stdout == "", "human path must write nothing to stdout (no envelope)"


def test_bare_crm_non_tty_exits_2(monkeypatch):
    """Bare `crm` with a non-TTY stdin (no --json, no CRM_NO_REPL) → exit 2.
    CliRunner already supplies a non-interactive stdin, so the isatty probe alone
    must suppress the REPL — error on stderr, stdout clean, never hang/exit 0."""
    monkeypatch.delenv("CRM_NO_REPL", raising=False)
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2
    assert _HELP_HINT in result.stderr
    assert result.stdout == "", "human path must write nothing to stdout (no envelope)"


def test_explicit_repl_still_launches(monkeypatch):
    """Explicit `crm repl` is unaffected by the guard: the REPL command is invoked
    even with CRM_NO_REPL set (which would suppress a *bare* crm) and a non-TTY
    stdin. The REPL is stubbed so the test asserts the hand-off, not the loop."""
    monkeypatch.setenv("CRM_NO_REPL", "1")  # would suppress a *bare* crm; not `crm repl`
    launched: list[bool] = []
    monkeypatch.setattr("crm.commands.repl.repl", _stub_repl(launched))
    result = CliRunner().invoke(cli, ["repl"])
    assert result.exit_code == 0, result.output
    assert launched == [True], "explicit `crm repl` must invoke the REPL, not the guard"


def test_bare_crm_interactive_tty_launches_repl(monkeypatch):
    """Interactive human: bare `crm` with a TTY stdin still drops into the REPL.
    The isatty probe is monkeypatched to True (CliRunner's stdin is never a TTY),
    and the REPL command is replaced with a recorder so the test does not enter
    the real interactive loop."""
    monkeypatch.delenv("CRM_NO_REPL", raising=False)
    monkeypatch.setattr("crm.cli._stdin_is_tty", lambda: True)
    launched: list[bool] = []
    monkeypatch.setattr("crm.commands.repl.repl", _stub_repl(launched))
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0, result.output
    assert launched == [True], "TTY bare `crm` must invoke the REPL, not the guard"
