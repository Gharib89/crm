"""Global flags placed after the subcommand get a position hint (#297).

A root-level global option (`--json`, `--dry-run`, `--profile`, …) is only
accepted before the subcommand. Placed after it, Click rejects it as a bare
`No such option` (and sometimes an actively misleading `Did you mean '--count'?`).
The root group rewrites that into a hint naming it as a global option and showing
the corrected `crm <flag> <command> ...` form. Genuinely unknown options are left
untouched. Parse-time only — no backend involved.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import cli

# Every root global option (introspected the same way the implementation does),
# so the parametrized test follows the option set automatically.
GLOBAL_FLAGS = sorted(
    opt
    for param in cli.params
    if hasattr(param, "opts")
    for opt in (*param.opts, *getattr(param, "secondary_opts", []))
    if opt.startswith("--")
)


def test_trailing_json_human_mode_hint():
    """`crm profile list --json` → exit 2, hint on stderr, no `Did you mean`."""
    result = CliRunner().invoke(cli, ["profile", "list", "--json"])
    assert result.exit_code == 2, result.output
    assert "'--json' is a global option" in result.stderr
    assert "crm --json profile list" in result.stderr
    assert "Did you mean" not in result.stderr
    # Human path: never a JSON envelope.
    with pytest.raises((ValueError, json.JSONDecodeError)):
        json.loads(result.stdout)


def test_trailing_json_after_query_drops_misleading_suggestion():
    """`crm query odata accounts --json` → hint; the bogus `--count` suggestion gone."""
    result = CliRunner().invoke(
        cli, ["query", "odata", "accounts", "--json"]
    )
    assert result.exit_code == 2, result.output
    assert "'--json' is a global option" in result.stderr
    # The hint rebuilds the command chain (positional args aren't part of it).
    assert "crm --json query odata ..." in result.stderr
    assert "--count" not in result.stderr


@pytest.mark.parametrize("flag", GLOBAL_FLAGS)
def test_every_global_flag_after_subcommand_hints(flag):
    """Each root global flag placed after a subcommand produces the position hint."""
    result = CliRunner().invoke(cli, ["profile", "list", flag])
    assert result.exit_code == 2, result.output
    assert f"{flag!r} is a global option" in result.stderr
    assert f"crm {flag} profile list" in result.stderr


def test_trailing_global_flag_json_mode_envelope():
    """`crm --json profile list --dry-run` → JSON usage envelope on stdout, exit 2.

    Root --json leads (so JSON mode is on); a *different* global flag trails and
    must surface the hint through the standard envelope, not raw stderr text.
    """
    result = CliRunner().invoke(
        cli, ["--json", "profile", "list", "--dry-run"]
    )
    assert result.exit_code == 2, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert "'--dry-run' is a global option" in env["error"]
    assert "crm --dry-run profile list" in env["error"]


def test_unknown_non_global_option_unchanged():
    """A genuinely unknown option keeps Click's standard error and suggestion."""
    result = CliRunner().invoke(cli, ["profile", "list", "--bogus"])
    assert result.exit_code == 2, result.output
    assert "No such option" in result.stderr
    assert "is a global option" not in result.stderr


def test_unknown_non_global_option_json_unchanged():
    """An unknown non-global option under --json still renders the plain envelope."""
    result = CliRunner().invoke(
        cli, ["--json", "profile", "list", "--bogus"]
    )
    assert result.exit_code == 2, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert "No such option" in env["error"]
    assert "is a global option" not in env["error"]
