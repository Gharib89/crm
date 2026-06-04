"""Non-interactive REPL guard (#65): bare `crm` (no subcommand) must fail fast
instead of opening the interactive REPL when the caller is clearly
non-interactive — under --json, an explicit CRM_NO_REPL opt-out, or a non-TTY
stdin. It exits 2 (usage) with a message pointing at `crm --help`, and never
hangs or exits 0. Explicit `crm repl` and an interactive human are unaffected.
"""
# pyright: basic
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli

_HELP_HINT = "crm --help"


def test_bare_json_exits_2_with_usage_envelope(monkeypatch):
    """Bare `crm --json` → exit 2 with a parseable {ok: false} usage envelope
    pointing at `crm --help`, not a REPL drop."""
    monkeypatch.delenv("CRM_NO_REPL", raising=False)
    result = CliRunner().invoke(cli, ["--json"])
    assert result.exit_code == 2, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert _HELP_HINT in env["error"]


def test_bare_crm_no_repl_env_exits_2_human(monkeypatch):
    """Bare `crm` with CRM_NO_REPL truthy → exit 2, plain-text error on stderr
    (no JSON envelope) pointing at `crm --help`."""
    monkeypatch.setenv("CRM_NO_REPL", "1")
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2, result.output
    assert _HELP_HINT in result.output
    # Human path must NOT emit a JSON envelope.
    try:
        json.loads(result.output)
        is_json = True
    except (ValueError, json.JSONDecodeError):
        is_json = False
    assert not is_json, "human path must not emit a JSON envelope"


def test_bare_crm_non_tty_exits_2(monkeypatch):
    """Bare `crm` with a non-TTY stdin (no --json, no CRM_NO_REPL) → exit 2.
    CliRunner already supplies a non-interactive stdin, so the isatty probe alone
    must suppress the REPL — it must never hang or exit 0."""
    monkeypatch.delenv("CRM_NO_REPL", raising=False)
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2, result.output
    assert _HELP_HINT in result.output


def test_explicit_repl_still_launches(monkeypatch):
    """Explicit `crm repl` is unaffected by the guard: it launches the REPL even
    under a non-TTY stdin (CliRunner). EOF on empty input ends the loop cleanly."""
    monkeypatch.setenv("CRM_NO_REPL", "1")  # would suppress a *bare* crm; not `crm repl`
    result = CliRunner().invoke(cli, ["repl"], input="")
    assert result.exit_code == 0, result.output
    assert "Session:" in result.output, "REPL banner/session line proves it launched"
    assert "no subcommand given" not in result.output, "guard must not fire on explicit repl"


def test_bare_crm_interactive_tty_launches_repl(monkeypatch):
    """Interactive human: bare `crm` with a TTY stdin still drops into the REPL.
    The isatty probe is monkeypatched to True (CliRunner's stdin is never a TTY),
    and the REPL command is replaced with a recorder so the test does not enter
    the real interactive loop."""
    monkeypatch.delenv("CRM_NO_REPL", raising=False)
    monkeypatch.setattr("crm.cli._stdin_is_tty", lambda: True)
    launched: list[bool] = []
    monkeypatch.setattr("crm.commands.repl.repl", lambda: launched.append(True))
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0, result.output
    assert launched == [True], "TTY bare `crm` must invoke the REPL, not the guard"
