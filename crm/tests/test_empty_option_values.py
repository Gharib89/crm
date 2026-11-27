# pyright: basic
"""An explicit empty value on an optional text or path flag is a usage error (#1417).

`--flag ""` used to read as an omitted flag wherever a command tested the value by
truthiness, so the flag was silently dropped: `ribbon export --solution ""` exported
the composed ribbon, `solution components --save ""` saved nothing. Every such option
now exits 2 on an empty value before a backend is built, except the few where an
empty value clears a field, which are listed in `_EXEMPT`.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.commands._helpers import EmptyAllowedOption

_EXEMPT = {
    ("profile add", "--url"),
    ("profile add", "--username"),
    ("profile add", "--domain"),
    ("profile add", "--tenant-id"),
    ("profile add", "--client-id"),
    ("profile add", "--adfs-url"),
    ("profile add", "--password"),
    ("profile add", "--client-secret"),
    ("profile add", "--publisher-prefix"),
    ("profile edit", "--username"),
    ("profile edit", "--domain"),
    ("profile edit", "--tenant-id"),
    ("profile edit", "--client-id"),
    ("profile edit", "--adfs-url"),
    ("profile edit", "--publisher-prefix"),
    ("workflow update", "--on-update-attributes"),
    ("metadata update-entity", "--description"),
    ("metadata update-attribute", "--description"),
    ("chart update", "--description"),
    ("solution set-version", "--description"),
    ("ribbon set-label", "--label"),
    ("ribbon set-label", "--tooltip-title"),
    ("ribbon set-label", "--tooltip-description"),
}
"""Options where an empty value keeps a meaning: it clears a stored field, or in the
`profile add` wizard it skips a prompt (blank domain is UPN, blank AD FS URL is
discovery, blank prefix is none) or, for a required value, prompts for it on a TTY.
"""


def _optional_text_options():
    """Every optional single-value text or path option in the CLI, as (path, opt)."""
    ctx = click.Context(cli)
    found = []

    def walk(cmd, path):
        for p in cmd.params:
            if (
                isinstance(p, click.Option)
                and not (p.is_flag or p.multiple or p.required or p.count)
                and p.nargs == 1
                and isinstance(p.type, click.types.StringParamType | click.Path)
            ):
                found.append((" ".join(path), p.opts[0]))
        if isinstance(cmd, click.Group):
            for name in cmd.list_commands(ctx):
                walk(cmd.get_command(ctx, name), [*path, name])

    walk(cli, [])
    return found


_OPTIONS = _optional_text_options()
_REJECTING = [o for o in _OPTIONS if o not in _EXEMPT]


@pytest.fixture
def no_backend(monkeypatch):
    def _boom(self):
        raise AssertionError("backend built past the empty-value guard")

    monkeypatch.setattr("crm.cli.CLIContext.backend", _boom)


def _argv(path, opt):
    return [*path.split(), opt, ""] if path else [opt, "", "connection", "whoami"]


@pytest.mark.parametrize(("path", "opt"), _REJECTING, ids=[f"{p} {o}" for p, o in _REJECTING])
def test_empty_value_is_a_usage_error(no_backend, path, opt):
    result = CliRunner().invoke(cli, _argv(path, opt))
    assert result.exit_code == 2, result.output
    assert opt in result.output
    assert "empty value" in result.output


def test_ribbon_export_empty_solution_builds_no_backend(no_backend):
    result = CliRunner().invoke(cli, ["--json", "ribbon", "export", "account", "--solution", ""])
    assert result.exit_code == 2, result.output
    assert "--solution" in result.output


def test_solution_components_empty_save_builds_no_backend(no_backend):
    result = CliRunner().invoke(cli, ["--json", "solution", "components", "S", "--save", ""])
    assert result.exit_code == 2, result.output
    assert "--save" in result.output


def test_exempt_options_are_marked_and_still_exist():
    ctx = click.Context(cli)
    marked = set()
    for path, opt in _EXEMPT:
        cmd: click.Command = cli
        for name in path.split():
            assert isinstance(cmd, click.Group)
            sub = cmd.get_command(ctx, name)
            assert sub is not None
            cmd = sub
        param = next(p for p in cmd.params if isinstance(p, click.Option) and opt in p.opts)
        if isinstance(param, EmptyAllowedOption):
            marked.add((path, opt))
    assert marked == _EXEMPT
