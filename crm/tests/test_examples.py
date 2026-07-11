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
    resolved path (or a root global option). Root global options are accepted
    both in their leading position (``crm --json entity ...``) and trailing;
    leading ones are skipped before the command-path walk. Drives the lazy root
    group via ``get_command``/``list_commands`` — its naive ``.commands`` set is
    empty until a subcommand loads, so a walker that read that directly would see
    nothing.
    """
    tokens = shlex.split(command)
    assert tokens and tokens[0] == "crm", f"example must start with 'crm': {command!r}"
    tokens = tokens[1:]

    ctx = click.Context(cli, info_name="crm")
    current: click.Command = cli
    path_cmds: list[click.Command] = [cli]

    # Root global options may lead the invocation, before the subcommand token
    # (`crm --json ...`). Skip that leading run so the path walk starts at the
    # command; a value-taking global (`--profile NAME`) also consumes its value.
    root_flags: set[str] = set()
    root_value_opts: set[str] = set()
    for p in cli.params:
        if isinstance(p, click.Option):
            (root_flags if p.is_flag else root_value_opts).update(p.opts, p.secondary_opts)
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        name = tokens[i].split("=", 1)[0]
        if name in root_flags:
            i += 1
        elif name in root_value_opts:
            i += 1 if "=" in tokens[i] else 2
        else:
            break  # unrecognized leading option — let the flag check below flag it

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


# Metadata verbs that write customizations — the backend refuses them without an
# explicit target solution (`--solution is required for customization writes`).
# The static resolver above can't see this runtime requirement, so a curated
# example that omits --solution parses fine yet fails for every user (verified
# live against the cloud org). Guard the gallery against that class of drift.
_CUSTOMIZATION_WRITE_VERBS = frozenset(
    {
        "add-attribute",
        "create-optionset",
        "update-optionset",
        "create-entity",
        "create-one-to-many",
        "create-many-to-many",
    }
)


def _command_path(command: str) -> list[str]:
    """Tokens of ``command`` with the leading ``crm`` and any leading root/global
    options stripped, so ``path[0]`` is the top-level group and ``path[1]`` the
    verb. Mirrors the leading-global skip in :func:`_resolve` so a curated example
    like ``crm --profile NAME metadata ...`` is still checked at its verb position,
    not silently skipped.
    """
    toks = shlex.split(command)
    if toks and toks[0] == "crm":
        toks = toks[1:]
    root_flags: set[str] = set()
    root_value_opts: set[str] = set()
    for p in cli.params:
        if isinstance(p, click.Option):
            (root_flags if p.is_flag else root_value_opts).update(p.opts, p.secondary_opts)
    i = 0
    while i < len(toks) and toks[i].startswith("-"):
        name = toks[i].split("=", 1)[0]
        if name in root_flags:
            i += 1
        elif name in root_value_opts:
            i += 1 if "=" in toks[i] else 2
        else:
            break
    return toks[i:]


def test_customization_write_examples_target_a_solution():
    for _group, ex in reg.listing(None):
        path = _command_path(ex.command)
        # Check the verb *position* (metadata <verb>), tolerating a leading global
        # option before the group, rather than mere token membership.
        if path[:1] == ["metadata"] and len(path) > 1 and path[1] in _CUSTOMIZATION_WRITE_VERBS:
            # Accept both the space form (--solution X) and Click's equals form
            # (--solution=X), so a valid equals-form example isn't misflagged.
            has_solution = any(t == "--solution" or t.startswith("--solution=") for t in path)
            assert has_solution, f"customization-write example must pass --solution: {ex.command!r}"


def test_optionset_examples_use_colon_option_syntax():
    # `--option` wants 'value:label' (or ':label'); the metadata option-set parser
    # (`_parse_value_labels`) raises a click.UsageError when the ':' is missing, so
    # an example using '=' never runs. Check both token shapes Click accepts:
    # `--option VALUE` and `--option=VALUE`.
    for _group, ex in reg.listing(None):
        toks = shlex.split(ex.command)
        for i, tok in enumerate(toks):
            value = None
            if tok == "--option" and i + 1 < len(toks):
                value = toks[i + 1]
            elif tok.startswith("--option="):
                value = tok.split("=", 1)[1]
            if value is not None:
                assert ":" in value, f"--option must be 'value:label', not '=': {ex.command!r}"


def test_gallery_is_non_empty_and_resolver_descends():
    # Two distinct guards, no brittle count: (1) a non-empty gallery so the
    # resolve test above can't pass vacuously over an empty loop; (2) proof the
    # resolver actually descends the lazy tree to a real leaf (a walker that read
    # the empty `.commands` set instead of get_command would fail this).
    assert reg.listing(None)
    leaf = _resolve("crm entity get accounts ID --select name")
    assert leaf.name == "get"


def test_resolver_accepts_leading_and_trailing_global_options():
    # A leading global option (before the subcommand) is skipped, incl. its value;
    # a trailing one validates against the root option set.
    assert _resolve("crm --json query count accounts").name == "count"
    assert _resolve("crm --profile NAME entity get accounts ID").name == "get"
    assert _resolve("crm entity get accounts ID --json").name == "get"


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
    monkeypatch.setattr(examples_mod, "_stdout_is_tty", lambda: True)
    responses = iter(["query", "crm query count accounts"])
    monkeypatch.setattr(examples_mod, "select_one", lambda *a, **k: next(responses))
    result = CliRunner().invoke(cli, ["examples"])
    assert result.exit_code == 0, result.output
    assert "crm query count accounts" in result.output


def test_picker_with_group_arg_skips_the_group_picker(monkeypatch):
    monkeypatch.setattr(examples_mod, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(examples_mod, "_stdout_is_tty", lambda: True)
    calls: list[str] = []

    def fake_select(title, items, default=None):
        calls.append(title)
        return items[0][0]  # choose the first offered value

    monkeypatch.setattr(examples_mod, "select_one", fake_select)
    result = CliRunner().invoke(cli, ["examples", "solution"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1  # example picker only — no group picker
    assert reg.examples_for("solution")[0].command in result.output


def test_piped_stdout_lists_instead_of_prompting(monkeypatch):
    # TTY stdin but non-TTY stdout (e.g. `crm examples | head`): must not invoke
    # the picker — fall through to the non-blocking listing instead.
    monkeypatch.setattr(examples_mod, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(examples_mod, "_stdout_is_tty", lambda: False)

    def boom(*_a, **_k):
        raise AssertionError("picker must not run when stdout is not a TTY")

    monkeypatch.setattr(examples_mod, "select_one", boom)
    result = CliRunner().invoke(cli, ["examples", "query"])
    assert result.exit_code == 0, result.output
    assert "crm query count accounts" in result.output


def test_picker_cancel_is_clean_failure(monkeypatch):
    monkeypatch.setattr(examples_mod, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(examples_mod, "_stdout_is_tty", lambda: True)
    monkeypatch.setattr(examples_mod, "select_one", lambda *a, **k: None)
    result = CliRunner().invoke(cli, ["examples", "entity"])
    assert result.exit_code == 1
