"""Near-miss top-level command groups get a "Did you mean ...?" suggestion (#298).

Mistyping a verb inside a subgroup (`crm profile lst`) already suggests the
closest command, because subgroups are stock Click groups with a populated
`self.commands`. The root group is lazy — its subcommands live in the lazy
registry, not `self.commands` — so a near-miss group name (`crm entit`) used to
get a bare `No such command 'entit'.` with no suggestion. The root group now
widens the candidate set to its full `list_commands`, restoring parity.
Parse-time only — no backend involved.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import cli


@pytest.mark.parametrize(
    "typo,suggestion",
    [("entit", "entity"), ("slution", "solution"), ("conection", "connection")],
)
def test_near_miss_top_level_group_suggests(typo, suggestion):
    """A one-off typo of a top-level group suggests the real name; exit 2.

    Click's wording is "Did you mean 'x'?" for a single close match and
    "(Did you mean one of: ...)" for several — reused unchanged (out of scope to
    alter), so we assert on the shared "Did you mean" lead plus the real name.
    """
    result = CliRunner().invoke(cli, [typo])
    assert result.exit_code == 2, result.output
    assert f"No such command {typo!r}." in result.stderr
    assert "Did you mean" in result.stderr
    assert repr(suggestion) in result.stderr


def test_suggestion_survives_trailing_args():
    """The suggestion fires on the bad group token even with a trailing verb."""
    result = CliRunner().invoke(cli, ["conection", "whoami"])
    assert result.exit_code == 2, result.output
    assert "Did you mean" in result.stderr
    assert "'connection'" in result.stderr


def test_no_close_match_gives_bare_message():
    """A typo with no close match keeps the plain error — no forced suggestion."""
    result = CliRunner().invoke(cli, ["zzzzz"])
    assert result.exit_code == 2, result.output
    assert "No such command 'zzzzz'." in result.stderr
    assert "Did you mean" not in result.stderr


def test_suggestion_under_json_envelope():
    """`crm --json entit` renders the suggestion through the JSON usage envelope."""
    result = CliRunner().invoke(cli, ["--json", "entit"])
    assert result.exit_code == 2, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert "No such command 'entit'." in env["error"]
    assert "Did you mean 'entity'?" in env["error"]


def test_subgroup_verb_suggestion_unchanged():
    """Existing verb-level suggestions inside a subgroup still work."""
    result = CliRunner().invoke(cli, ["profile", "lst"])
    assert result.exit_code == 2, result.output
    assert "No such command 'lst'." in result.stderr
    assert "Did you mean 'list'?" in result.stderr
