"""Dual-position global options (#818, ADR 0025).

Five global options — `--json`, `--fields`, `--jq`, `--profile`, `--dry-run` — are
valid BOTH before and after the subcommand; position is pure syntax with zero
semantic difference. They are injected into every leaf command at the root
resolution seam (`crm.cli._inject_dualpos_tree`), NOT by argv rewriting.

These tests pin the acceptance criteria: byte-identical behavior across positions,
the both-positions usage error, cross-position `--fields`/`--jq` exclusion, the
collision-skip for leaves declaring their own same-named option, the argv-scanner's
accepted false positive, REPL stickiness of a leaf `--profile`, that a root-only
flag (`--stage-only`) is NOT injected, and the injection tree-walk sanity check.

`describe` is the workhorse fixture command: pure Click-tree introspection, no
backend and no profile needed, with a rich deterministic `data` payload for
`--fields`/`--jq` — so an isolated CRM_HOME keeps every assertion hermetic.
"""

# pyright: basic
from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from crm.cli import _DUALPOS_TOKENS, CLIContext, _json_mode_active, cli

pytestmark = pytest.mark.usefixtures("isolated_home")

DUALPOS = set(_DUALPOS_TOKENS.values())


def _run(args):
    return CliRunner().invoke(cli, args)


def _resolve_leaf(*path: str) -> click.Command:
    """Resolve a command path through the live (lazy, injecting) tree to its leaf."""
    cc = click.Context(cli, info_name="crm")
    node: click.Command = cli
    for name in path:
        assert isinstance(node, click.Group)
        found = node.get_command(cc, name)
        assert found is not None, f"no such command: {name}"
        node = found
    return node


# ── Dual-position works ──────────────────────────────────────────────────────


def test_trailing_json_works():
    """`crm profile list --json` is now valid and emits the JSON envelope (exit 0)."""
    r = _run(["profile", "list", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["ok"] is True


def test_trailing_dry_run_accepted():
    """`--dry-run` after the subcommand is accepted and sets dry-run state."""
    r = _run(["--json", "describe", "entity", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["meta"]["dry_run"] is True


def test_trailing_jq_implies_json_and_shapes():
    """`--jq` after the subcommand runs the program and implies --json (no root flag)."""
    r = _run(["describe", "entity", "--jq", ".commands | length"])
    assert r.exit_code == 0, r.output
    env = json.loads(r.stdout)  # implies --json → parseable envelope
    assert env["ok"] is True
    assert isinstance(env["data"], int) and env["data"] > 0


def test_trailing_fields_projects():
    """`--fields` after the subcommand projects the data payload."""
    r = _run(["--json", "describe", "entity", "--fields", "commands"])
    assert r.exit_code == 0, r.output
    assert set(json.loads(r.stdout)["data"].keys()) == {"commands"}


# ── Byte-identical behavior: each flag at root vs leaf ───────────────────────

# (root-argv, leaf-argv) pairs — only the one flag under test changes position;
# any co-required --json stays pinned at the root so the diff is purely positional.
_EQUALITY_CASES = {
    "json": (["--json", "describe", "entity"], ["describe", "entity", "--json"]),
    "dry_run": (
        ["--json", "--dry-run", "describe", "entity"],
        ["--json", "describe", "entity", "--dry-run"],
    ),
    "profile": (
        ["--json", "--profile", "nope", "describe", "entity"],
        ["--json", "describe", "entity", "--profile", "nope"],
    ),
    "fields": (
        ["--json", "--fields", "commands", "describe", "entity"],
        ["--json", "describe", "entity", "--fields", "commands"],
    ),
    "jq": (
        ["--json", "--jq", ".commands | length", "describe", "entity"],
        ["--json", "describe", "entity", "--jq", ".commands | length"],
    ),
}


@pytest.mark.parametrize("kind", sorted(_EQUALITY_CASES))
def test_root_and_leaf_positions_are_byte_identical(kind):
    """Each of the five flags produces byte-identical output at root and leaf."""
    root_args, leaf_args = _EQUALITY_CASES[kind]
    root = _run(root_args)
    leaf = _run(leaf_args)
    assert root.exit_code == leaf.exit_code == 0, (root.output, leaf.output)
    assert root.stdout == leaf.stdout


# ── Both-positions → usage error (exit 2) ────────────────────────────────────

# Args that supply the flag at BOTH positions; each must be a usage error.
_BOTH_POSITION_CASES = {
    "json": ["--json", "describe", "entity", "--json"],
    "dry_run": ["--dry-run", "describe", "entity", "--dry-run"],
    "profile": ["--profile", "a", "describe", "entity", "--profile", "b"],
    "fields": ["--fields", "commands", "describe", "entity", "--fields", "commands"],
    "jq": ["--jq", ".", "describe", "entity", "--jq", "."],
}


@pytest.mark.parametrize("kind", sorted(_BOTH_POSITION_CASES))
def test_both_positions_is_usage_error(kind):
    """Supplying a flag before AND after the subcommand is a uniform usage error."""
    r = _run(_BOTH_POSITION_CASES[kind])
    assert r.exit_code == 2, r.output
    token = _DUALPOS_TOKENS[kind]
    assert f"{token} was provided both before and after the subcommand" in r.output


def test_both_positions_json_envelope():
    """Under --json, a both-positions error renders as the JSON error envelope."""
    r = _run(["--json", "describe", "entity", "--json"])
    assert r.exit_code == 2, r.output
    env = json.loads(r.stdout)
    assert env["ok"] is False
    assert "provided both before and after the subcommand" in env["error"]


# ── Cross-position --fields / --jq mutual exclusion ──────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        ["--fields", "commands", "describe", "entity", "--jq", "."],  # root fields + leaf jq
        ["--jq", ".", "describe", "entity", "--fields", "commands"],  # root jq   + leaf fields
        ["describe", "entity", "--fields", "commands", "--jq", "."],  # both at leaf
    ],
)
def test_fields_jq_mutually_exclusive_cross_position(args):
    r = _run(args)
    assert r.exit_code == 2, r.output
    assert "--fields and --jq are mutually exclusive" in r.output


# ── Collision-skip: a leaf's own same-named option keeps its meaning ─────────


def test_set_password_keeps_local_required_profile():
    """`profile set-password` declares its own required `--profile`, so the injected
    dual-position `--profile` is skipped and the local one still binds.
    """
    setpw = _resolve_leaf("profile", "set-password")
    profile_opts = [
        p for p in setpw.params if isinstance(p, click.Option) and "--profile" in p.opts
    ]
    assert len(profile_opts) == 1
    assert profile_opts[0].required is True
    assert profile_opts[0].name == "profile_name"  # the local option, not _dualpos_*
    # Missing the required local --profile is still a usage error (not silently
    # satisfied by an injected optional one).
    r = _run(["profile", "set-password", "--password", "x"])
    assert r.exit_code == 2, r.output


def test_profile_add_keeps_local_password():
    """`profile add --password` is the local option (--password is not dual-position)."""
    add = _resolve_leaf("profile", "add")
    pw = [p for p in add.params if isinstance(p, click.Option) and "--password" in p.opts]
    assert len(pw) == 1 and pw[0].name == "password_opt"


def test_session_group_local_session_flag_unaffected():
    """`--session` is root-only (not one of the five), so nothing is injected for it."""
    assert "--session" not in DUALPOS


# ── argv scanner: the accepted false positive (decision 5) ───────────────────


def test_scanner_detects_trailing_json():
    assert _json_mode_active(["entity", "get", "accounts", "--json"]) is True


def test_scanner_skips_root_value_option_values():
    # `--profile foo` consumes `foo`; a real trailing --json is still detected.
    assert _json_mode_active(["--profile", "foo", "entity", "get", "x", "--json"]) is True


def test_scanner_accepted_false_positive_leaf_value():
    """A literal '--json' passed as a LEAF option's value is (acceptably) mistaken
    for the flag — arbitrary leaf value-options are not enumerated in the scanner.
    """
    assert _json_mode_active(["entity", "get", "accounts", "--select", "--json"]) is True


def test_scanner_skips_fields_and_jq_values():
    """`--fields`/`--jq` are root value-taking options, so a '--json' passed as their
    value is NOT mistaken for the flag (Copilot round 2).
    """
    assert _json_mode_active(["--fields", "--json", "profile", "list"]) is False
    assert _json_mode_active(["--jq", "--json", "profile", "list"]) is False
    # A real trailing --json after a --fields projection is still detected.
    assert _json_mode_active(["--fields", "name", "profile", "list", "--json"]) is True


# ── REPL: leaf --profile sticky; root-only flag NOT injected ─────────────────


def test_leaf_profile_sticky_across_repl_lines():
    """The REPL reuses one CLIContext across lines (cli.main obj=ctx); a leaf
    `--profile` set on one line stays set on the next bare line.
    """
    ctx = CLIContext()

    def line(argv):
        try:
            cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")
        except (SystemExit, click.exceptions.Exit, click.ClickException):
            pass

    line(["profile", "list", "--profile", "myorg"])
    assert ctx.profile_name == "myorg"
    line(["profile", "list"])  # bare line omits --profile
    assert ctx.profile_name == "myorg"  # still sticky


def test_stage_only_not_injected_and_still_hints():
    """`--stage-only` is out of scope: not injected into leaves, and the position
    hint still fires when it trails the subcommand.
    """
    entget = _resolve_leaf("entity", "get")
    tokens = {t for p in entget.params if isinstance(p, click.Option) for t in p.opts}
    assert "--stage-only" not in tokens
    r = _run(["entity", "get", "accounts", "--stage-only"])
    assert r.exit_code == 2, r.output
    assert "'--stage-only' is a global option" in r.stderr


# ── Passive-guard JSON signal is position-independent (Copilot round 1) ──────


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "describe", "entity"],
        ["describe", "entity", "--json"],
        ["--jq", ".", "describe", "entity"],
        ["describe", "entity", "--jq", "."],
    ],
)
def test_json_guard_signal_set_for_json_either_position(argv):
    """The whole-argv guard signal (which gates the passive update-check) is True for
    a --json/--jq in EITHER position, so root and leaf placement behave identically —
    not just at emit time (the update notice) but for the background-probe kickoff.
    """
    _run(argv)
    assert cli._json_for_guards is True


def test_json_guard_signal_clear_for_plain_invocation():
    _run(["describe", "entity"])
    assert cli._json_for_guards is False


# ── Injection tree-walk sanity ───────────────────────────────────────────────


def test_injection_reaches_every_leaf():
    """Walking via list_commands/get_command (root is lazy — a naive `.commands`
    walk would see 0), well over 100 leaves carry all five dual-position tokens.
    """
    cc = click.Context(cli, info_name="crm")

    def leaves(group):
        out = []
        for name in group.list_commands(cc):
            cmd = group.get_command(cc, name)
            if cmd is None:
                continue
            if isinstance(cmd, click.Group):
                out += leaves(cmd)
            else:
                out.append(cmd)
        return out

    all_leaves = leaves(cli)

    def carries_all_five(cmd):
        tokens = {t for p in cmd.params if isinstance(p, click.Option) for t in p.opts}
        return DUALPOS <= tokens

    carrying = [c for c in all_leaves if carries_all_five(c)]
    assert len(carrying) > 100
    assert carrying == all_leaves  # every leaf carries all five
