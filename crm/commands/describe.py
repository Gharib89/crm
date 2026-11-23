"""`crm describe` — machine-readable command/option/choice discovery (#63).

Walks the live Click command tree from the root group and emits a structured
catalogue of every command, its options/arguments, Choice enums, defaults, and
envvars. Pure introspection of live Click objects — no mkdocs-click dependency
and no D365 connection required.

Two choices are not visible from the code. The root group's sticky global
options (--json, --dry-run, --profile and so on) belong to no subcommand but
apply to every invocation, so they are surfaced separately as `root_options`.
Naming an excluded leaf (repl) explicitly is a usage error, so the exclusion
the full walk applies cannot be bypassed. An unknown GROUP is a caller mistake
caught before any backend call, so it exits 2 (ADR 0001). In human mode the
catalogue renders as a flat table, since emit() would otherwise truncate the
nested lists into an unreadable JSON blob.
"""

# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, cli, pass_ctx

_EXCLUDED = {"repl"}
"""Leaves left out of the catalogue: the interactive REPL takes no options worth
enumerating and is not meant to be driven programmatically."""

_UNSET_DEFAULT = click.Option(["--x"]).default
"""Click's internal UNSET sentinel, stored as a param's `default` when none was
declared. It is not a re-exported symbol, so the singleton is captured from a
throwaway option (public API) and compared by identity in `_default`."""


def _choices(param: click.Parameter) -> list[str] | None:
    """Return a Choice param's enum values verbatim, or None for non-Choice types."""
    return list(param.type.choices) if isinstance(param.type, click.Choice) else None


def _default(param: click.Parameter):
    """Serialize a param's default, mapping Click's internal UNSET sentinel to None.

    A param with no declared default holds Click's UNSET sentinel; emitted verbatim
    it stringifies (via the JSON encoder's `default=str`) to the literal
    "Sentinel.UNSET", a phantom value that misleads an agent generating invocations
    from the catalogue. Report a real default or an explicit null instead.
    """
    default = param.default
    return None if default is _UNSET_DEFAULT else default


def _serialize_option(opt: click.Option) -> dict:
    """Serialize an option; `secondary_opts` is the off-form of a boolean flag pair
    (--no-publish for --publish/--no-publish), without which every --no-* flag
    drops out of the catalogue.
    """
    return {
        "name": opt.name,
        "opts": list(opt.opts),
        "secondary_opts": list(opt.secondary_opts),
        "type": opt.type.name,
        "required": bool(opt.required),
        "is_flag": bool(opt.is_flag),
        "multiple": bool(opt.multiple),
        "choices": _choices(opt),
        "default": _default(opt),
        "envvar": opt.envvar,
    }


def _serialize_argument(arg: click.Argument) -> dict:
    return {
        "name": arg.name,
        "type": arg.type.name,
        "required": bool(arg.required),
        "multiple": bool(arg.multiple),
        "choices": _choices(arg),
        "default": _default(arg),
    }


def _describe_command(cmd: click.Command, path: list[str]) -> dict:
    """Describe one command. Hidden options (deprecated aliases, internal escape
    hatches) are deliberately off the public surface, so they are dropped.
    """
    args = [_serialize_argument(p) for p in cmd.params if isinstance(p, click.Argument)]
    params = [
        _serialize_option(p) for p in cmd.params if isinstance(p, click.Option) and not p.hidden
    ]
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
    root_options = [
        _serialize_option(p) for p in cli.params if isinstance(p, click.Option) and not p.hidden
    ]
    if group:
        cmd = None if group in _EXCLUDED else cli.get_command(click_ctx, group)
        if cmd is None:
            raise click.UsageError(f"No such command {group!r}.")
        commands = [_describe_command(cmd, [group])]
        if isinstance(cmd, click.Group):
            commands.extend(_walk(cmd, click_ctx, [group]))
    else:
        commands = _walk(cli, click_ctx, [])

    if ctx.json_mode:
        ctx.emit(True, data={"root_options": root_options, "commands": commands})
        return
    rows = [[c["path"], "group" if c["is_group"] else "command", c["help"]] for c in commands]
    ctx.emit(
        True,
        table={"headers": ["command", "kind", "help"], "rows": rows},
        meta={"commands": len(commands), "root_options": len(root_options)},
    )
