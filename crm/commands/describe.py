"""`crm describe` — machine-readable command/option/choice discovery (#63).

Walks the live Click command tree from the root group and emits a structured
catalogue of every command, its options/arguments, Choice enums, defaults, and
envvars. Pure introspection of live Click objects — no mkdocs-click dependency
and no D365 connection required.
"""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, cli, pass_ctx

# The interactive REPL leaf is excluded from the catalogue: it takes no options
# worth enumerating and is not meant to be driven programmatically.
_EXCLUDED = {"repl"}


def _choices(param: click.Parameter) -> list[str] | None:
    """Return a Choice param's enum values verbatim, or None for non-Choice types."""
    return list(param.type.choices) if isinstance(param.type, click.Choice) else None


def _serialize_option(opt: click.Option) -> dict:
    return {
        "name": opt.name,
        "opts": list(opt.opts),
        # Off-form of a boolean flag-pair (--no-publish for --publish/--no-publish);
        # empty for plain options. Without this the catalogue drops every --no-* flag.
        "secondary_opts": list(opt.secondary_opts),
        "type": opt.type.name,
        "required": bool(opt.required),
        "is_flag": bool(opt.is_flag),
        "multiple": bool(opt.multiple),
        "choices": _choices(opt),
        "default": opt.default,
        "envvar": opt.envvar,
    }


def _serialize_argument(arg: click.Argument) -> dict:
    return {
        "name": arg.name,
        "type": arg.type.name,
        "required": bool(arg.required),
        "multiple": bool(arg.multiple),
        "choices": _choices(arg),
        "default": arg.default,
    }


def _describe_command(cmd: click.Command, path: list[str]) -> dict:
    args = [_serialize_argument(p) for p in cmd.params if isinstance(p, click.Argument)]
    params = [_serialize_option(p) for p in cmd.params if isinstance(p, click.Option)]
    return {
        "name": path[-1],
        "path": " ".join(path),
        "help": cmd.get_short_help_str() or "",
        "is_group": isinstance(cmd, click.Group),
        "args": args,
        "params": params,
    }


def _walk(group: click.Group, ctx: click.Context, path: list[str]) -> list[dict]:
    """Recurse the Click tree under `group`, returning a flat list of command dicts."""
    entries: list[dict] = []
    for name in group.list_commands(ctx):
        if name in _EXCLUDED:
            continue
        cmd = group.get_command(ctx, name)
        if cmd is None:
            continue
        sub_path = [*path, name]
        entries.append(_describe_command(cmd, sub_path))
        if isinstance(cmd, click.Group):
            entries.extend(_walk(cmd, ctx, sub_path))
    return entries


@click.command("describe")
@click.argument("group", required=False)
@pass_ctx
def describe_cmd(ctx: CLIContext, group: str | None):
    """Emit a machine-readable catalogue of all commands, options, and choices.

    With no argument, walks the whole tree. With GROUP, walks only that command's
    subtree — importing just that one module (a lazy win over the full walk).
    """
    click_ctx = click.Context(cli, info_name="crm")
    # Root sticky global options (--json, --dry-run, --profile, …) are not part of
    # any subcommand but apply to every invocation, so surface them separately.
    root_options = [
        _serialize_option(p) for p in cli.params if isinstance(p, click.Option)
    ]
    if group:
        # Excluded leaves (repl) are absent from the catalogue everywhere — naming
        # one explicitly must not bypass the exclusion the full walk applies.
        cmd = None if group in _EXCLUDED else cli.get_command(click_ctx, group)
        if cmd is None:
            ctx.emit(False, error=f"No such command {group!r}.")
            return  # unreachable: emit(False) raises Exit (narrows cmd for the checker)
        commands = [_describe_command(cmd, [group])]
        if isinstance(cmd, click.Group):
            commands.extend(_walk(cmd, click_ctx, [group]))
    else:
        commands = _walk(cli, click_ctx, [])

    if ctx.json_mode:
        ctx.emit(True, data={"root_options": root_options, "commands": commands})
        return
    # Human mode: a flat table is far more legible than the truncated JSON blob
    # emit() would otherwise render for the nested lists.
    rows = [
        [c["path"], "group" if c["is_group"] else "command", c["help"]]
        for c in commands
    ]
    ctx.emit(
        True,
        table={"headers": ["command", "kind", "help"], "rows": rows},
        meta={"commands": len(commands), "root_options": len(root_options)},
    )
