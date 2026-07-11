"""Root-only global flags placed after the subcommand get a position hint (#297).

A root-only global option (`--password`, `--stage-only`, `--session`, …) is only
accepted before the subcommand. Placed after it, Click rejects it as a bare
`No such option` (and sometimes an actively misleading `Did you mean '--count'?`).
The root group rewrites that into a hint naming it as a global option and showing
the corrected `crm <flag> <command> ...` form. Genuinely unknown options are left
untouched. Parse-time only — no backend involved.

Since #818 the five **dual-position** global options (`--json`, `--fields`, `--jq`,
`--profile`, `--dry-run`) are valid *after* the subcommand too, so they no longer
hint — they are injected into every leaf and just work. Their positive behavior is
pinned in test_dual_position_global_options.py; here we assert the hint still fires
for every *root-only* flag.
"""

# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import _DUALPOS_TOKENS, cli

# Every root global option (introspected the same way the implementation does),
# so the parametrized test follows the option set automatically.
GLOBAL_FLAGS = sorted(
    opt
    for param in cli.params
    if hasattr(param, "opts")
    for opt in (*param.opts, *getattr(param, "secondary_opts", []))
    if opt.startswith("--")
)

# The five dual-position flags accept the after-the-subcommand position (#818), so
# they are NOT hinted; every other root flag still is. Derived from the code so the
# split can never drift from the actual dual-position set.
ROOT_ONLY_FLAGS = [f for f in GLOBAL_FLAGS if f not in set(_DUALPOS_TOKENS.values())]


def test_trailing_stage_only_human_mode_hint():
    """`crm profile list --stage-only` → exit 2, hint on stderr, no `Did you mean`."""
    result = CliRunner().invoke(cli, ["profile", "list", "--stage-only"])
    assert result.exit_code == 2, result.output
    assert "'--stage-only' is a global option" in result.stderr
    assert "crm --stage-only profile list" in result.stderr
    assert "Did you mean" not in result.stderr
    # Human path: never a JSON envelope.
    with pytest.raises((ValueError, json.JSONDecodeError)):
        json.loads(result.stdout)


def test_trailing_root_only_after_query_drops_misleading_suggestion():
    """`crm query odata accounts --stage-only` → hint; the bogus `--count` gone."""
    result = CliRunner().invoke(cli, ["query", "odata", "accounts", "--stage-only"])
    assert result.exit_code == 2, result.output
    assert "'--stage-only' is a global option" in result.stderr
    # The hint rebuilds the command chain (positional args aren't part of it).
    assert "crm --stage-only query odata ..." in result.stderr
    assert "--count" not in result.stderr


@pytest.mark.parametrize("flag", ROOT_ONLY_FLAGS)
def test_every_root_only_flag_after_subcommand_hints(flag):
    """Each root-only global flag placed after a subcommand produces the hint."""
    result = CliRunner().invoke(cli, ["profile", "list", flag])
    assert result.exit_code == 2, result.output
    assert f"{flag!r} is a global option" in result.stderr
    assert f"crm {flag} profile list" in result.stderr


def test_trailing_root_only_flag_json_mode_envelope():
    """`crm --json profile list --stage-only` → JSON usage envelope on stdout, exit 2.

    Root --json leads (so JSON mode is on); a root-only global flag trails and must
    surface the hint through the standard envelope, not raw stderr text.
    """
    result = CliRunner().invoke(cli, ["--json", "profile", "list", "--stage-only"])
    assert result.exit_code == 2, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert "'--stage-only' is a global option" in env["error"]
    assert "crm --stage-only profile list" in env["error"]


def test_unknown_non_global_option_unchanged():
    """A genuinely unknown option keeps Click's standard error and suggestion."""
    result = CliRunner().invoke(cli, ["profile", "list", "--bogus"])
    assert result.exit_code == 2, result.output
    assert "No such option" in result.stderr
    assert "is a global option" not in result.stderr


def test_unknown_non_global_option_json_unchanged():
    """An unknown non-global option under --json still renders the plain envelope."""
    result = CliRunner().invoke(cli, ["--json", "profile", "list", "--bogus"])
    assert result.exit_code == 2, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert "No such option" in env["error"]
    assert "is a global option" not in env["error"]
