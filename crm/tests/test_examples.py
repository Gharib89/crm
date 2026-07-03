"""`crm examples` — curated runnable example gallery + anti-drift gate (#658).

The anti-drift tests are the point of keeping the gallery in code: every curated
example is resolved against the *live* Click tree, so an example that references
a removed command or flag fails CI. The command-behavior tests cover the JSON
contract, the group filter, and the TTY picker flow.
"""
# pyright: basic
import json
import shlex

import click
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.commands import examples as examples_mod
from crm.core import examples as reg


def _resolve(command: str) -> click.Command:
    """Resolve a full ``crm ...`` invocation against the live Click tree.

    Returns the resolved leaf command. Raises ``AssertionError`` if the command
    path does not exist or any flag is not a real option of a command on the
    resolved path (or a root global option). Drives the lazy root group via
    ``get_command``/``list_commands`` — its naive ``.commands`` set is empty until
    a subcommand loads, so a walker that read that directly would see nothing.
    """
    tokens = shlex.split(command)
    assert tokens and tokens[0] == "crm", f"example must start with 'crm': {command!r}"
    tokens = tokens[1:]

    ctx = click.Context(cli, info_name="crm")
    current: click.Command = cli
    path_cmds: list[click.Command] = [cli]

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            break
        if isinstance(current, click.Group):
            sub = current.get_command(ctx, tok)
            if sub is not None:
                current = sub
                path_cmds.append(sub)
                i += 1
                continue
        # A non-option token that is not a subcommand: a positional argument when
        # we're already at a leaf command, else an unknown command name.
        assert not isinstance(current, click.Group), f"unknown command {tok!r} in: {command!r}"
        break

    assert not isinstance(current, click.Group), (
        f"example resolves to a group, not a runnable command: {command!r}"
    )

    valid_opts: set[str] = set()
    for cmd in path_cmds:
        for p in cmd.params:
            if isinstance(p, click.Option):
                valid_opts.update(p.opts)
                valid_opts.update(p.secondary_opts)

    for tok in tokens[i:]:
        if tok.startswith("-") and tok != "-":
            name = tok.split("=", 1)[0]
            assert name in valid_opts, f"unknown option {name!r} in: {command!r}"
    return current


# ── Anti-drift gate ─────────────────────────────────────────────────────────
def test_every_curated_example_resolves_against_live_cli():
    for group, ex in reg.listing(None):
        resolved = _resolve(ex.command)
        assert resolved is not None, (group, ex.command)


def test_gallery_has_a_sanity_floor():
    # Guards the lazy-root trap: a walker that saw the empty `.commands` set would
    # yield an empty gallery and the resolve test would pass vacuously. A concrete
    # floor plus a proof the resolver descends to a real leaf catches that.
    assert len(reg.listing(None)) >= 20
    leaf = _resolve("crm entity get accounts ID --select name")
    assert leaf.name == "get"


def test_resolver_rejects_unknown_command():
    with pytest.raises(AssertionError):
        _resolve("crm entity frobnicate ID")


def test_resolver_rejects_unknown_flag():
    with pytest.raises(AssertionError):
        _resolve("crm entity get accounts ID --does-not-exist")


# ── JSON / non-interactive listing ───────────────────────────────────────────
def test_json_listing_is_the_whole_gallery():
    result = CliRunner().invoke(cli, ["--json", "examples"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert len(envelope["data"]) == len(reg.listing(None))


def test_json_row_contract_is_group_command_description_only():
    # The workflow tag is display-only; it must never leak into the JSON contract.
    result = CliRunner().invoke(cli, ["--json", "examples"])
    data = json.loads(result.output)["data"]
    assert data
    for row in data:
        assert set(row) == {"group", "command", "description"}


def test_json_group_filter_narrows_to_one_group():
    result = CliRunner().invoke(cli, ["--json", "examples", "solution"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data and all(row["group"] == "solution" for row in data)


def test_unknown_group_is_clean_failure():
    result = CliRunner().invoke(cli, ["--json", "examples", "nope"])
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert "nope" in envelope["error"]


# ── Interactive TTY picker ────────────────────────────────────────────────────
def test_picker_prints_selected_command(monkeypatch):
    monkeypatch.setattr(examples_mod, "_stdin_is_tty", lambda: True)
    responses = iter(["query", "crm query count accounts"])
    monkeypatch.setattr(examples_mod, "select_one", lambda *a, **k: next(responses))
    result = CliRunner().invoke(cli, ["examples"])
    assert result.exit_code == 0, result.output
    assert "crm query count accounts" in result.output


def test_picker_with_group_arg_skips_the_group_picker(monkeypatch):
    monkeypatch.setattr(examples_mod, "_stdin_is_tty", lambda: True)
    calls: list[str] = []

    def fake_select(title, items, default=None):
        calls.append(title)
        return items[0][0]  # choose the first offered value

    monkeypatch.setattr(examples_mod, "select_one", fake_select)
    result = CliRunner().invoke(cli, ["examples", "solution"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1  # example picker only — no group picker
    assert reg.examples_for("solution")[0].command in result.output


def test_picker_cancel_is_clean_failure(monkeypatch):
    monkeypatch.setattr(examples_mod, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(examples_mod, "select_one", lambda *a, **k: None)
    result = CliRunner().invoke(cli, ["examples", "entity"])
    assert result.exit_code == 1
